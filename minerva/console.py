import collections
import threading
import time
from json import JSONDecodeError
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import humanize
from rich import box
from rich.console import Console, Group
from rich.markup import escape
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from minerva.constants import HISTORY_LINES

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

    def __init__(self) -> None:
        self._leaderboard_last_fetch = 0  # Only use in update_rank_loop (update_rank)

        # All following variables are locked by _lock
        self.history: collections.deque = collections.deque(maxlen=HISTORY_LINES)
        self.active: dict[int, Any] = {}  # file_id -> dict
        self._lock = threading.Lock()
        self._session_start = time.monotonic()
        self._page = 0
        self._total_done = 0
        self._total_fails = 0
        self._total_bytes = 0
        self._username = None
        self._leaderboard_cache: tuple[int | None, int | None] | tuple[None, None] = (None, None)

    def job_start(self, job: dict[str, Any], label: str) -> None:
        now = time.monotonic()
        with self._lock:
            job.update(
                dict(
                    label=label,
                    status="OK",
                    size=job["size"] or 0,
                    downloaded=0,
                    uploaded=0,
                    waiting=True,
                    start_time=now,
                    prev_done=0,
                    prev_time=now,
                    speed=0.0,
                )
            )
            self.active[job["file_id"]] = job

    def job_update(
        self,
        file_id: int,
        status: str,
        size: int | None = None,
        downloaded: int | None = None,
        uploaded: int | None = None,
        waiting: bool | None = None,
    ) -> None:
        try:
            now = time.monotonic()
            with self._lock:
                if file_id not in self.active:
                    return
                job = self.active[file_id]
                if downloaded is not None:
                    dt = now - job["prev_time"]
                    if dt >= 0.5:
                        dd = downloaded - job["prev_done"]
                        job["speed"] = dd / dt if dt > 0 else job["speed"]
                        job["prev_done"] = downloaded
                        job["prev_time"] = now
                    job["downloaded"] = downloaded
                if uploaded is not None:
                    job["uploaded"] = uploaded
                job["status"] = status
                if size is not None:
                    job["size"] = size
                job["waiting"] = waiting
        except Exception:
            console.print_exception()

    def job_done(self, file_id: int, label: str, ok: bool, note: str = "") -> None:
        with self._lock:
            job = self.active.pop(file_id, None)
            job["waiting"] = False
            if ok:
                self._total_done += 1
                if job and job["size"]:
                    self._total_bytes += job["size"]
            else:
                self._total_fails += 1
            icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
            color = "green" if ok else "red"
            entry = f"{icon} [{color}]{label}[/{color}]"
            if note:
                entry += f"  [dim]{note}[/dim]"
            self.history.append(entry)

    @staticmethod
    def effective_speed(job: dict[str, Any]) -> int:
        age = time.monotonic() - job["prev_time"]
        decay = max(0.0, 1 - age / 3)
        return max(0.0, job["speed"] * decay)

    def get_stats(self) -> Table:
        now = time.monotonic()

        with self._lock:
            snapshot = list(self.active.values())
            elapsed_total = now - self._session_start
            done_count = self._total_done
            fail_count = self._total_fails
            total_bytes = self._total_bytes
            speed = sum(self.effective_speed(x) for x in snapshot)
            rank, uploaded = self._leaderboard_cache
            username = self._username

        h = int(elapsed_total // 3600)
        m = int((elapsed_total % 3600) // 60)
        s = int(elapsed_total % 60)

        def get_size(speed: int | float | None) -> str:
            return humanize.naturalsize(speed or 0, binary=True, gnu=False, format="%.2f").replace(" Bytes", "b")

        stats = Table.grid(expand=True)
        stats.add_column(justify="left")
        stats.add_column(justify="right")
        stats.add_row(
            f"[cyan]{username} #{rank or '--'}[/cyan] [dim]({get_size(float(uploaded or 0))})[/dim] "
            + f"Uploads: [dim]{done_count} ({get_size(total_bytes)})[/dim] "
            + f"Failures: [dim]{fail_count}[/dim]",
            f"[blue]Speed: {get_size(speed)}/s[/blue] " + f"[dim]{h:02d}:{m:02d}:{s:02d}[/dim]",
        )

        return stats

    def update_rank(self) -> None:
        now = time.monotonic()
        personal_stats: tuple[int | None, int | None] | tuple[None, None] | None = None

        with self._lock:
            previous_leaderboard = self._leaderboard_cache
            username = self._username

        if username:
            if now - self._leaderboard_last_fetch > 180 or previous_leaderboard is None:
                try:
                    personal_stats = next(
                        (
                            (x.get("rank"), x.get("total_bytes"))
                            for x in httpx.get(
                                "https://minerva-archive.org/api/leaderboard?limit=10000", timeout=30
                            ).json()
                            if x["discord_username"] == username
                        ),
                        (None, None),
                    )
                except (JSONDecodeError, httpx.ConnectError, httpx.ReadTimeout) as e:
                    console.print(f"[yellow]Currently unable to refresh leaderboard rank: {e}.")
                self._leaderboard_last_fetch = now

        if personal_stats is not None:
            with self._lock:
                self._leaderboard_cache = personal_stats

    def __rich__(self) -> Group:
        now = time.monotonic()

        with self._lock:
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

        for job in visible_jobs:
            try:
                color = {"OK": "blue", "RT": "magenta"}.get(job["status"], "white")
                size = job["size"]
                waiting = job["waiting"]
                speed = self.effective_speed(job)
                elapsed = now - job["start_time"]
                elapsed_str = f"[dim]{int(elapsed // 60):02d}:{int(elapsed % 60):02d}[/dim]"

                if not waiting:
                    size = job["size"] or 1
                    speed_str = f"[dim]{humanize.naturalsize(speed, gnu=True)}/s[/dim]" if speed > 0 else "[dim]—[/dim]"
                    size = max(job["size"] or 0, 1)
                    dl = max(job.get("downloaded", 0), 0)
                    ul = max(job.get("uploaded", 0), 0)
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
                    bar = f"[green]{'█' * ul_w}[/green][blue]{'█' * dl_w}[/blue][dim]{'░' * remaining}[/dim] {pct:4.0f}%"
                else:
                    spin = _SPINNER[int(now * 8) % len(_SPINNER)]
                    speed_str = f"[{color}]{spin} Waiting[/{color}]"

                    note = ""
                    if job["status"] == "RT":
                        note = "to retry after fail"
                    bar = f"[{color}]{note}[/{color}]"

                file_str = Path(urlparse(job["label"]).path).name
                if job.get("is_cached"):
                    file_str = f"[orange1]Cache:[/orange1] {escape(file_str)}"

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
                console.print_exception()

        parts: list = []

        if history_lines:
            parts.extend(Text.from_markup(line) for line in history_lines)
            parts.append(Rule(style="dim"))
        parts.append(self.get_stats())
        parts.append(Rule(style="dim"))
        parts.append(table)

        if pages > 1:
            parts.append(
                Text.from_markup(f"[dim]Page {self._page + 1}/{pages} [← prev | → next][/dim]", justify="center")
            )

        return Group(*parts)
