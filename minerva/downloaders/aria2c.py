import asyncio
import re
from pathlib import Path

from humanfriendly import parse_size

from minerva.constants import ARIA2C
from minerva.downloaders import Downloader, ProgressCallback

ARIA_PROGRESS_REGEX = re.compile(r"\[#\w+\s+([^/]+)/([^\(]+)\((\d+)%\).*?DL:([^\s\]]+)(?:\s+ETA:([^\]]+))?")

class Aria2c(Downloader):
    async def __call__(
        self, url: str, dest: Path, size: int, connections: int, pre_allocation: str, on_progress: ProgressCallback
    ) -> None:
        if not ARIA2C:
            raise EnvironmentError("Cannot download with aria2c as it could not be found...")

        proc = await asyncio.create_subprocess_exec(
            str(ARIA2C),
            f"--max-connection-per-server={connections}",
            f"--split={connections}",
            f"--file-allocation={pre_allocation}",
            "--min-split-size=1M",
            "--dir",
            str(dest.parent),
            "--out",
            dest.name,
            "--auto-file-renaming=false",
            "--allow-overwrite=true",
            "--console-log-level=notice",
            "--summary-interval=1",
            "--retry-wait=3",
            "--max-tries=5",
            "--timeout=120",
            "--connect-timeout=15",
            "--continue",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        while True:
            if proc.stdout:
                line = await proc.stdout.readline()
                if not line:
                    break
                line_str = line.decode("utf8", errors="replace")
                match = ARIA_PROGRESS_REGEX.search(line_str)
                if match:
                    on_progress(parse_size(match.group(1).strip()), size or parse_size(match.group(2).strip()))
        await proc.wait()

        if proc.returncode != 0:
            raise RuntimeError(f"aria2c exit {proc.returncode}")


__all__ = ["Aria2c"]
