from pathlib import Path
from typing import Protocol


class ProgressCallback(Protocol):
    def __call__(self, current: int, total: int) -> None: ...


class Downloader(Protocol):
    async def __call__(
        self, url: str, dest: Path, size: int, connections: int, pre_allocation: str, on_progress: ProgressCallback
    ) -> None: ...


__all__ = ["Downloader", "ProgressCallback"]
