import collections
import threading
import time

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
        self.history: collections.deque = collections.deque(maxlen=HISTORY_LINES)
        self.active: dict = {}  # file_id -> dict
        self._lock = threading.Lock()
        self._session_start = time.monotonic()
        self._total_done = 0
        self._total_bytes = 0
        self._username = None

    def job_start(self, file_id: int, label: str) -> None:
        now = time.monotonic()
        with self._lock:
            self.active[file_id] = dict(
                label=label,
                status="DL",
                size=0,
                done=0,
                start_time=now,
                prev_done=0,
                prev_time=now,
                speed=0.0,
            )

    def job_update(self, file_id: int, status: str, size: int | bool | None = None, done: int | None = None) -> None:
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

    def job_done(self, file_id: int, label: str, ok: bool, note: str = "") -> None:
        with self._lock:
            job = self.active.pop(file_id, None)
            if ok:
                self._total_done += 1
                if job and isinstance(job["size"], int):
                    self._total_bytes += job["size"]
            icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
            color = "green" if ok else "red"
            entry = f"{icon} [{color}]{label}[/{color}]"
            if note:
                entry += f"  [dim]{note}[/dim]"
            self.history.append(entry)

    def get_stats(self) -> Table:
        now = time.monotonic()

        with self._lock:
            elapsed_total = now - self._session_start
            done_count = self._total_done
            total_bytes = self._total_bytes

        h = int(elapsed_total // 3600)
        m = int((elapsed_total % 3600) // 60)
        s = int(elapsed_total % 60)

        if not self._username:
            token = load_token()
            if token:
                token_dec = jwt.decode(token, options={"verify_signature": False})
                self._username = token_dec.get("username", "")

        rank: int | None = None
        uploaded: int | None = None
        if self._username:
            rank, uploaded = next(
                (
                    (x.get("rank"), x.get("total_bytes"))
                    for x in httpx.get("https://minerva-archive.org/api/leaderboard?limit=10000").json()
                    if x["discord_username"] == self._username
                )
            )

        stats = Table.grid(expand=True)
        stats.add_column(justify="left")
        stats.add_column(justify="right")
        stats.add_row(
            f"Uptime: [dim]{h:02d}:{m:02d}:{s:02d}[/dim] "
            + f"Uploaded: [dim]{done_count} ({humanize.naturalsize(total_bytes)})[/dim]",
            f"{self._username} #{rank} [dim]({humanize.naturalsize(float(uploaded)) if uploaded else 0})[/dim]",
        )

        return stats

    def __rich__(self) -> Group:
        now = time.monotonic()

        with self._lock:
            snapshot = list(self.active.values())
            history_lines = list(self.history)

        # Active jobs table
        table = Table(box=box.SIMPLE, show_header=True, expand=True, header_style="bold dim", padding=(0, 1))
        table.add_column("", width=3)
        table.add_column("File")
        table.add_column("Size", width=10, justify="right")
        table.add_column("Speed", width=10, justify="right")
        table.add_column("Progress", width=26)

        for info in snapshot:
            st = info["status"]
            color = {"DL": "cyan", "UL": "yellow", "RT": "magenta"}.get(st, "white")
            size = info["size"]
            done = info["done"]
            speed = info["speed"]
            elapsed = now - info["start_time"]

            speed_str = f"[dim]{humanize.naturalsize(speed, gnu=True)}/s[/dim]" if speed > 0 else "[dim]—[/dim]"

            if size:
                pct = min(1.0, done / size)
                bar_w = 14
                filled = int(bar_w * pct)
                bar = (
                    f"[{color}]"
                    + "█" * filled
                    + f"[/{color}]"
                    + "[dim]"
                    + "░" * (bar_w - filled)
                    + "[/dim]"
                    + f" {pct * 100:4.0f}%"
                )
                size_str = humanize.naturalsize(size)
            else:
                spin = _SPINNER[int(now * 8) % len(_SPINNER)]
                elapsed_str = f"{int(elapsed // 60):02d}:{int(elapsed % 60):02d}"
                bar = f"[{color}]{spin}[/{color}] [dim]{humanize.naturalsize(done)} — {elapsed_str}[/dim]"
                size_str = "[dim]?[/dim]"

            table.add_row(
                f"[{color}]{st}[/{color}]",
                info["label"],
                size_str,
                speed_str,
                bar,
            )

        parts: list = []
        if history_lines:
            parts.extend(Text.from_markup(line) for line in history_lines)
            parts.append(Rule(style="dim"))
        parts.append(table)
        parts.append(Rule(style="dim"))
        parts.append(self.get_stats())
        return Group(*parts)
