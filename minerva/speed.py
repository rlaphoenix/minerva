import asyncio
import time

import httpx
from rich.progress import Progress


async def download_worker(client: httpx.AsyncClient, url: str, progress: Progress, task: int) -> int:
    downloaded = 0

    async with client.stream(
        method="GET",
        url=url,
        follow_redirects=True,
        headers={"User-Agent": "Hyperscrape Speed Tester/v1 (Created By Hackerdude)"},
    ) as response:
        async for chunk in response.aiter_bytes(8192):
            size = len(chunk)
            downloaded += size
            progress.update(task, advance=size)

    return downloaded


async def test_download_speed(url: str, workers: int = 16) -> float:
    async with httpx.AsyncClient(timeout=None) as client:
        head = await client.head(url, follow_redirects=True)
        length = int(head.headers.get("Content-Length", 0))

        progress = Progress()
        progress.start()

        task = progress.add_task("Downloading...", total=length * workers if length else None)

        start = time.monotonic()

        tasks = [asyncio.create_task(download_worker(client, url, progress, task)) for _ in range(workers)]

        results = await asyncio.gather(*tasks)

        end = time.monotonic()

        progress.stop()

    total_downloaded = sum(results)
    time_taken = end - start

    speed = total_downloaded / time_taken
    return speed
