import collections
import threading
import time
from json import JSONDecodeError
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import humanize
import jwt
from rich import box
from rich.console import Console, Group
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from minerva.auth import load_token
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
                    status="DL",
                    size=job["size"] or 0,
                    done=0,
                    waiting=True,
                    start_time=now,
                    prev_done=0,
                    prev_time=now,
                    speed=0.0,
                )
            )
            self.active[job["file_id"]] = job

    def job_update(
        self, file_id: int, status: str, size: int | None = None, done: int | None = None, waiting: bool | None = None
    ) -> None:
        now = time.monotonic()
        with self._lock:
            if file_id not in self.active:
                return
            job = self.active[file_id]
            if done is not None:
                dt = now - job["prev_time"]
                if dt >= 0.5:
                    dd = done - job["prev_done"]
                    job["speed"] = dd / dt if dt > 0 else job["speed"]
                    job["prev_done"] = done
                    job["prev_time"] = now
                job["done"] = done
            job["status"] = status
            if size is not None:
                job["size"] = size
            job["waiting"] = waiting

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
            dl_speed = sum(self.effective_speed(x) for x in snapshot if x["status"] == "DL")
            ul_speed = sum(self.effective_speed(x) for x in snapshot if x["status"] == "UL")
            rank, uploaded = self._leaderboard_cache
            username = self._username

        h = int(elapsed_total // 3600)
        m = int((elapsed_total % 3600) // 60)
        s = int(elapsed_total % 60)

        if not username:
            token = load_token()
            if token:
                token_dec = jwt.decode(token, options={"verify_signature": False})
                username = token_dec.get("username", "")
                with self._lock:
                    self._username = username

        def get_size(speed: int | float | None) -> str:
            return humanize.naturalsize(speed or 0, binary=True, gnu=False, format="%.2f").replace(" Bytes", "b")

        stats = Table.grid(expand=True)
        stats.add_column(justify="left")
        stats.add_column(justify="right")
        stats.add_row(
            f"[cyan]{username} #{rank or '--'}[/cyan] [dim]({get_size(float(uploaded or 0))})[/dim] "
            + f"Uploads: [dim]{done_count} ({get_size(total_bytes)})[/dim] "
            + f"Failures: [dim]{fail_count}[/dim]",
            f"[green]⬇{get_size(dl_speed)}/s[/green] [blue]⬆{get_size(ul_speed)}/s[/blue] "
            + f"[dim]{h:02d}:{m:02d}:{s:02d}[/dim]",
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

        table.add_column("Status", width=2)  # fit UL/DL/RT
        table.add_column(
            "File", overflow="ellipsis", no_wrap=True, justify="left", ratio=1
        )  # expand to fill space, but prefer shorter names
        table.add_column("Size", justify="right", width=10)  # fit sizes up to 10,0000 TB
        table.add_column("Speed", justify="right", width=10)  # fit speeds up to 10000 T/s
        table.add_column("Progress", justify="left", width=20)  # fit 14-width progress bar + percentage
        table.add_column("Uptime", justify="right", width=5)  # fit mm:ss up to 99:59

        for info in visible_jobs:
            color = {"DL": "cyan", "UL": "yellow", "RT": "magenta"}.get(info["status"], "white")
            status = f"[{color}]{info['status']}[/{color}]"
            size = info["size"]
            done = info["done"]
            waiting = info["waiting"]
            speed = self.effective_speed(info)
            elapsed = now - info["start_time"]
            elapsed_str = f"[dim]{int(elapsed // 60):02d}:{int(elapsed % 60):02d}[/dim]"

            if not waiting:
                pct = min(1.0, done / (size or 1))
                bar_w = 14
                filled = int(bar_w * pct)
                speed_str = f"[dim]{humanize.naturalsize(speed, gnu=True)}/s[/dim]" if speed > 0 else "[dim]—[/dim]"
                bar = (
                    f"[{color}]"
                    + "█" * filled
                    + f"[/{color}]"
                    + "[dim]"
                    + "░" * (bar_w - filled)
                    + "[/dim]"
                    + f" {pct * 100:4.0f}%"
                )
            else:
                spin = _SPINNER[int(now * 8) % len(_SPINNER)]
                speed_str = f"[{color}]{spin} Waiting[/{color}]"

                note = ""
                if info["status"] == "DL":
                    note = "patiently in queue"
                elif info["status"] == "UL":
                    note = "on server to respond"
                elif info["status"] == "RT":
                    note = "to retry after fail"
                bar = f"[{color}]{note}[/{color}]"

            file_str = Path(urlparse(info["label"]).path).name
            if info.get("is_cached"):
                file_str = f"[orange1]Cache:[/orange1] {file_str}"

            size_str = humanize.naturalsize(size)

            table.add_row(
                status,
                file_str,
                size_str,
                speed_str,
                bar,
                elapsed_str,
            )

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
