import asyncio
import collections
import logging
import time
from json import JSONDecodeError
from pathlib import Path
from urllib.parse import urlparse

import httpx
import humanize
from rich import box
from rich.console import Console, Group
from rich.table import Table
from rich.text import Text

from minerva.auth import load_token
from minerva.constants import HISTORY_LINES, LEADERBOARD_ENDPOINT, USER_AGENT
from minerva.ws_message import ChunkInfo, JobState

console = Console()

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class WorkerDisplay:
    """
    Terminal display:
      • recent completed/failed jobs (oldest scrolls off, hidden when empty)
      • divider rule
      • one row per active worker slot
      • session stats footer
    """

    log = logging.getLogger(__file__)

    def __init__(self) -> None:
        self._leaderboard_last_fetch = 0.0  # Only use in update_rank_loop (update_rank)

        # All following variables are locked by _lock
        self.history: collections.deque = collections.deque(maxlen=HISTORY_LINES)
        self.active: dict[str, tuple[ChunkInfo, JobState]] = {}
        self.connected: bool = False
        self.downtime: float = 0.0
        self._lock = asyncio.Lock()
        self._session_start = time.monotonic()
        self._page = 0
        self._total_done = 0
        self._total_fails = 0
        self._total_stops = 0
        self._total_bytes = 0
        self._username = None
        self._discord_id = None
        self._leaderboard_cache: tuple[int | None, int | None] | tuple[None, None] = (None, None)

    async def clear(self) -> None:
        async with self._lock:
            self.history.clear()
            self.active.clear()
            self.connected = False
            self.downtime = 0.0
            self._session_start = time.monotonic()
            self._page = 0
            self._total_done = 0
            self._total_fails = 0
            self._total_stops = 0
            self._total_bytes = 0

    async def remove_jobs(self, worker_id: str) -> None:
        async with self._lock:
            self.active = {
                file_id: (job, state) for file_id, (job, state) in self.active.items() if state.worker_id != worker_id
            }

    async def job_start(self, job: ChunkInfo, label: str, worker_id: str) -> None:
        now = time.monotonic()
        state = JobState(
            worker_id=worker_id,
            label=label,
            status="OK",
            size=(job.end - job.start) or 0,
            downloaded=0,
            uploaded=0,
            waiting=True,
            start_time=now,
            prev_downloaded=0,
            prev_uploaded=0,
            prev_time=now,
            download_speed=0.0,
            upload_speed=0.0,
        )
        async with self._lock:
            self.active[job.file_id] = (job, state)

    async def job_update(
        self,
        file_id: str,
        status: str,
        size: int | None = None,
        downloaded: int | None = None,
        uploaded: int | None = None,
        waiting: bool | None = None,
    ) -> None:
        try:
            now = time.monotonic()
            async with self._lock:
                if file_id not in self.active:
                    return
                job, state = self.active[file_id]
                update_rates = False
                dt = now - state.prev_time
                if dt >= 0.5:
                    state.prev_time = now
                    update_rates = True
                if downloaded is not None:
                    state.downloaded = downloaded
                    if update_rates:
                        dd = downloaded - state.prev_downloaded
                        state.download_speed = dd / dt
                        state.prev_downloaded = downloaded
                if uploaded is not None:
                    state.uploaded = uploaded
                    if update_rates:
                        uu = uploaded - state.prev_uploaded
                        state.upload_speed = uu / dt
                        state.prev_uploaded = uploaded
                state.status = status
                if size is not None:
                    state.size = size
                if waiting is not None:
                    state.waiting = waiting
        except Exception:
            self.log.exception("Error updating job state for file_id %s", file_id, exc_info=True)

    async def job_done(self, file_id: str, label: str, ok: bool, note: str = "") -> None:
        async with self._lock:
            _, state = self.active.pop(file_id, (None, None))
            if state:
                state.waiting = False
            if ok:
                self._total_done += 1
                if state and state.size:
                    self._total_bytes += state.size
            else:
                if note == "Stopping...":
                    self._total_stops += 1
                else:
                    self._total_fails += 1
            icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
            color = "green" if ok else "red"
            entry = f"{icon} [{color}]{label}[/{color}]"
            if note:
                entry += f"  [dim]{note}[/dim]"
            self.history.append(entry)

    @staticmethod
    def effective_speeds(state: JobState) -> tuple[float, float]:
        age = time.monotonic() - state.prev_time
        decay = max(0.0, 1 - age / 3)
        return (max(0.0, state.download_speed * decay), max(0.0, state.upload_speed * decay))

    @staticmethod
    def get_timestamp(since: float, in_seconds: bool = False) -> str:
        elapsed = time.monotonic() - since
        if in_seconds:
            return str(int(elapsed))
        h = int(elapsed // 3600)
        m = int((elapsed % 3600) // 60)
        s = int(elapsed % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def get_stats(self) -> Table:
        snapshot = list(self.active.values())
        done_count = self._total_done
        fail_count = self._total_fails
        stop_count = self._total_stops
        total_bytes = self._total_bytes
        dl, ul = zip((0, 0), *[self.effective_speeds(state) for _, state in snapshot])
        download_speed = sum(dl)
        upload_speed = sum(ul)
        rank, uploaded = self._leaderboard_cache
        username = self._username

        def get_size(speed: int | float | None) -> str:
            return humanize.naturalsize(speed or 0, binary=True, gnu=False, format="%.2f").replace(" Bytes", "b")

        leaderboard_stats = f"[cyan]{username} #{rank or '--'}[/cyan] [dim]({get_size(float(uploaded or 0))})[/dim]"
        upload_stats = f"Uploads: [dim]{done_count} ({get_size(total_bytes)})[/dim]"
        fail_stats = f"Failures: [dim]{fail_count}[/dim]"
        stop_stats = f"Stopped: [dim]{stop_count}[/dim]"
        uptime = f"Uptime: [dim]{self.get_timestamp(self._session_start)}[/dim]"

        connection_stats = ""
        if self.connected:
            connection_stats = (
                f"Speed: [blue]↓ {get_size(download_speed)}/s[/blue] [green]↑ {get_size(upload_speed)}/s[/green]"
            )
        elif self.downtime < 1:
            connection_stats = "[cyan]Connecting...[/cyan]"
        else:
            connection_stats = f"[red]Down for {self.get_timestamp(self.downtime, in_seconds=True)}s[/red]"

        stats = Table(
            box=box.HEAVY_HEAD,
            show_header=False,
            expand=True,
            border_style="dim",
        )
        stats.add_column(justify="left", ratio=1)
        stats.add_column(justify="right")
        stats.add_column(justify="right", width=16)
        stats.add_row(
            f"{leaderboard_stats} {upload_stats} {fail_stats} {stop_stats}",
            connection_stats,
            uptime,
        )

        return stats

    async def update_rank(self, server: str) -> None:
        now = time.monotonic()
        personal_stats: tuple[int | None, int | None] | tuple[None, None] | None = None

        async with self._lock:
            previous_leaderboard = self._leaderboard_cache
            if not self._username or not self._discord_id:
                token = load_token()
                r = httpx.get(
                    url="https://discord.com/api/users/@me",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=30,
                )
                if not r.is_success:
                    self.log.warning(
                        f"[yellow]Unable to fetch Discord Username ({r.status_code})[/yellow]", exc_info=True
                    )
                    return
                data = r.json()
                self._username = data["global_name"]
                self._discord_id = data["id"]
            username = self._username
            discord_id = self._discord_id

        if username and discord_id:
            if now - self._leaderboard_last_fetch > 180 or previous_leaderboard is None:
                try:
                    res = httpx.get(
                        url=f"{server}{LEADERBOARD_ENDPOINT}",
                        headers={"User-Agent": USER_AGENT},
                        timeout=30,
                    )
                    leaderboard = sorted(res.json(), key=lambda x: x["downloaded_bytes"], reverse=True)
                    for rank, item in enumerate(leaderboard, start=1):
                        item["rank"] = rank
                    personal_stats = next(
                        (
                            (x["rank"], x["downloaded_bytes"] or 0.0)
                            for x in leaderboard
                            if x["discord_username"] == username and discord_id in x["avatar_url"]
                        ),
                        (None, None),
                    )
                except (JSONDecodeError, httpx.ConnectError, httpx.ReadTimeout) as e:
                    self.log.warning(
                        f"[yellow]Currently unable to refresh leaderboard rank: {e}.[/yellow]", exc_info=True
                    )
                self._leaderboard_last_fetch = now

        if personal_stats is not None:
            async with self._lock:
                self._leaderboard_cache = personal_stats

    def __rich__(self) -> Group:
        now = time.monotonic()

        snapshot = list(self.active.values())
        history_lines = list(self.history)

        height = console.size.height

        # estimate non-table lines
        non_table_lines = len(history_lines)
        if history_lines:
            non_table_lines += 1  # rule
        non_table_lines += 2  # stats + rule

        available_rows = max(3, height - non_table_lines - 3)

        total_rows = len(snapshot)
        pages = max(1, (total_rows + available_rows - 1) // available_rows)
        self._page = max(0, min(self._page, pages - 1))

        start = self._page * available_rows
        end = start + available_rows
        visible_jobs = snapshot[start:end]

        table = Table(
            box=box.SIMPLE,
            show_header=False,
            expand=True,
            header_style="bold dim",
            padding=(0, 0),
        )

        table.add_column(
            "File", overflow="ellipsis", no_wrap=True, justify="left", ratio=1
        )  # expand to fill space, but prefer shorter names
        table.add_column("Size", justify="right", width=10)  # fit sizes up to 10,0000 TB
        table.add_column("Speed", justify="right", width=10)  # fit speeds up to 10000 T/s
        table.add_column("Progress", justify="left", width=20)  # fit 14-width progress bar + percentage
        table.add_column("Uptime", justify="right", width=5)  # fit mm:ss up to 99:59

        for job, state in visible_jobs:
            try:
                color = {"OK": "blue", "RT": "magenta"}.get(state.status, "white")
                size = state.size
                waiting = state.waiting
                dl, ul = self.effective_speeds(state)
                speed = dl + ul
                elapsed = now - state.start_time
                elapsed_str = f"[dim]{int(elapsed // 60):02d}:{int(elapsed % 60):02d}[/dim]"

                if not waiting:
                    size = state.size or 1
                    speed_str = f"[dim]{humanize.naturalsize(speed, gnu=True)}/s[/dim]" if speed > 0 else "[dim]—[/dim]"
                    size = max(state.size or 0, 1)
                    dl = max(state.downloaded or 0, 0)
                    ul = max(state.uploaded or 0, 0)
                    dl = dl - ul
                    dl_ratio = min(dl / size, 1)
                    ul_ratio = min(ul / size, 1)
                    bar_w = 14
                    dl_w = int(bar_w * dl_ratio)
                    ul_w = int(bar_w * ul_ratio)
                    if dl_w + ul_w > bar_w:
                        ul_w = max(0, bar_w - dl_w)
                    remaining = bar_w - dl_w - ul_w
                    pct = (ul / size) * 100
                    bar = (
                        f"[green]{'█' * ul_w}[/green][blue]{'█' * dl_w}[/blue][dim]{'░' * remaining}[/dim] {pct:4.0f}%"
                    )
                else:
                    spin = _SPINNER[int(now * 8) % len(_SPINNER)]
                    speed_str = f"[{color}]{spin} Waiting[/{color}]"

                    note = ""
                    if state.status == "RT":
                        note = "to retry after fail"
                    bar = f"[{color}]{note}[/{color}]"

                file_str = Text(Path(urlparse(state.label).path).name)

                if size:
                    size_str = humanize.naturalsize(size)
                else:
                    size_str = "—"

                table.add_row(
                    file_str,
                    size_str,
                    speed_str,
                    bar,
                    elapsed_str,
                )
            except Exception:
                self.log.exception("Error rendering job state for file_id %s", job.file_id, exc_info=True)

        parts: list = []

        if history_lines:
            parts.extend(Text.from_markup(line) for line in history_lines)
        parts.append(self.get_stats())
        parts.append(table)

        if pages > 1:
            left_arrow = "← " if self._page > 0 else ""
            right_arrow = " →" if self._page < pages - 1 else ""
            parts.append(
                Text.from_markup(f"[dim]{left_arrow}Page {self._page + 1}/{pages}{right_arrow}[/dim]", justify="center")
            )

        return Group(*parts)
