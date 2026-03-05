import asyncio
from pathlib import Path
from random import random
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
import jwt
import readchar
from humanfriendly import parse_size
from humanize import naturalsize
from rich.live import Live

from minerva.auth import auth_headers
from minerva.cache import job_cache
from minerva.console import WorkerDisplay, console
from minerva.constants import ARIA2C, QUEUE_PREFETCH
from minerva.error_handling import _raise_if_upgrade_required
from minerva.jobs import process_job
from minerva.size_map import get_size

_STOP = object()


async def input_loop(display: WorkerDisplay) -> None:
    while True:
        key = await asyncio.to_thread(readchar.readkey)

        with display._lock:
            if key == readchar.key.RIGHT:
                display._page += 1
            elif key == readchar.key.LEFT:
                display._page -= 1


async def update_rank_loop(display: WorkerDisplay) -> None:
    while True:
        try:
            display.update_rank()
        except Exception:
            # Just in case one error misses a catch → asyncio voids it otherwise
            console.print_exception()
        await asyncio.sleep(20)


async def worker_loop(
    server_url: str,
    upload_server_url: str,
    token: str,
    temp_dir: Path,
    concurrency: int,
    batch_size: int,
    dl_retries: int,
    ul_retries: int,
    max_cache_size: str,
    aria2c_connections: int,
    pre_allocation: str,
    min_job_size: str,
    max_job_size: str,
    keep_files: bool,
) -> None:
    token_dec = jwt.decode(token, options={"verify_signature": False})

    console.print(f"Username:       {token_dec.get('username', '-')}")
    console.print(f"Server:         {server_url}")
    console.print(f"Upload Server:  {upload_server_url}")
    console.print(f"Temp Directory: {temp_dir}")
    console.print(f"Concurrency:    {concurrency}")
    console.print(f"Batch Size:     {batch_size}")
    console.print(f"Connections:    {aria2c_connections}")
    console.print(f"Retries:        Download: {dl_retries} Upload: {ul_retries}")
    console.print(f"Downloader:     {'aria2c' if ARIA2C else 'httpx'}")
    console.print(f"Min job size:   {naturalsize(parse_size(min_job_size)) if min_job_size else 'N/A'}")
    console.print(f"Max job size:   {naturalsize(parse_size(max_job_size)) if max_job_size else 'N/A'}")
    console.print(f"Max cache size: {naturalsize(parse_size(max_cache_size)) if max_cache_size else 'N/A'}")
    console.print(f"Keep files:     {'yes' if keep_files else 'no'}")
    console.print()

    if not ARIA2C:
        console.print(
            "[yellow]Warning: aria2c not found, downloads will be slower and the progress bar wont work.[/yellow]"
        )
        console.print(
            "[yellow]         Install aria2c for better performance. On Windows run: winget install aria2.aria2[/yellow]"
        )

    temp_dir.mkdir(parents=True, exist_ok=True)

    queue: asyncio.Queue = asyncio.Queue(maxsize=concurrency * QUEUE_PREFETCH)
    stop_event = asyncio.Event()
    seen_ids: set[int] = set()
    seen_lock = asyncio.Lock()
    max_queue_size: int = parse_size(max_cache_size) if max_cache_size else 0
    queue_size: int = 0
    display = WorkerDisplay()
    queue_lock = asyncio.Lock()

    cache_available = asyncio.Event()
    cache_available.set()

    min_job_size_bytes = parse_size(min_job_size) if min_job_size else None
    max_job_size_bytes = parse_size(max_job_size) if max_job_size else None

    async def queue_jobs(jobs: list[dict[str, Any]]) -> int:
        nonlocal queue_size

        jobs_queued = 0
        for job in jobs:
            file_id = job["file_id"]
            async with seen_lock:
                if file_id in seen_ids:
                    continue
                seen_ids.add(file_id)

            if not job.get("size") and job.get("url"):
                job["size"] = get_size(job["url"])

            if job.get("size"):
                if min_job_size_bytes and (job["size"] < min_job_size_bytes):
                    console.print(
                        f"[yellow]Skipping job {Path(urlparse(unquote(job['url'])).path).name} "
                        f"({naturalsize(job['size'])} < "
                        f"{naturalsize(min_job_size_bytes)})[/yellow]"
                    )
                    continue
                if max_job_size_bytes and (job["size"] > max_job_size_bytes):
                    console.print(
                        f"[yellow]Skipping job {Path(urlparse(unquote(job['url'])).path).name} "
                        f"({naturalsize(job['size'])} > "
                        f"{naturalsize(max_job_size_bytes)})[/yellow]"
                    )
                    continue
                if max_queue_size:
                    async with queue_lock:
                        if (queue_size + job["size"]) > max_queue_size:
                            console.print(
                                f"[yellow]Skipping job {Path(urlparse(unquote(job['url'])).path).name} "
                                f" ({max_queue_size} cache size limit)[/yellow]"
                            )
                            continue
                        queue_size += job["size"]

            await queue.put(job)
            jobs_queued += 1

        return jobs_queued

    # ── Producer ────────────────────────────────────────────────────────────
    async def producer() -> None:
        no_jobs_warned = False
        async with httpx.AsyncClient(timeout=30) as api:
            while not stop_event.is_set():
                async with queue_lock:
                    bloated = max_queue_size and (queue_size >= max_queue_size)
                    free_slots = max(0, queue.maxsize - queue.qsize())

                if bloated:
                    cache_available.clear()
                    await cache_available.wait()
                    continue

                if queue.qsize() >= queue.maxsize // 2:
                    await asyncio.sleep(0.5)
                    continue

                cached_jobs = job_cache.list()
                jobs_added = 0
                if cached_jobs:
                    jobs_added = await queue_jobs(cached_jobs)
                    if jobs_added == 0:
                        await asyncio.sleep(0.5)

                fetch_count = max(0, min(batch_size, free_slots - jobs_added))
                if fetch_count > 0:
                    try:
                        resp = await api.get(
                            f"{server_url}/api/jobs",
                            params={"count": fetch_count},
                            headers=auth_headers(token),
                        )
                        if resp.status_code == 426:
                            _raise_if_upgrade_required(resp)
                        if resp.status_code == 401:
                            console.print("[red]Token expired. Run: minerva login")
                            stop_event.set()
                            break
                        resp.raise_for_status()
                        jobs = resp.json().get("jobs", [])
                        if jobs:
                            jobs_added = await queue_jobs(jobs)
                            if jobs_added == 0:
                                await asyncio.sleep(5)
                        else:
                            if not no_jobs_warned:
                                console.print("[dim]Server has no jobs available, waiting 30s…")
                                no_jobs_warned = True
                            await asyncio.sleep(12 + random() * 8)
                            continue
                    except httpx.HTTPError as e:
                        console.print(f"[red]Server error: {e}. Retrying in 10s…")
                        await asyncio.sleep(6 + random() * 4)
                else:
                    await asyncio.sleep(2)

            no_jobs_warned = False

        for _ in range(concurrency):
            await queue.put(_STOP)

    # ── Workers ─────────────────────────────────────────────────────────────
    async def worker() -> None:
        nonlocal queue_size
        while True:
            job = await queue.get()
            if job is _STOP:
                queue.task_done()
                return
            try:
                await process_job(
                    server_url,
                    upload_server_url,
                    token,
                    job,
                    temp_dir,
                    keep_files,
                    dl_retries,
                    ul_retries,
                    aria2c_connections,
                    pre_allocation,
                    display,
                )
            finally:
                if max_queue_size and job.get("size"):
                    async with queue_lock:
                        queue_size -= job["size"]
                        if queue_size < max_queue_size:
                            cache_available.set()
                async with seen_lock:
                    seen_ids.discard(job["file_id"])
                queue.task_done()

    with Live(display, console=console, refresh_per_second=4, screen=False):
        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
        producer_task = asyncio.create_task(producer())
        input_loop_task = asyncio.create_task(input_loop(display))
        update_rank_task = asyncio.create_task(update_rank_loop(display))
        try:
            await asyncio.gather(producer_task, input_loop_task, update_rank_task, *workers)
        except KeyboardInterrupt:
            console.print("\n[yellow]Shutting down…")
            stop_event.set()
            producer_task.cancel()
            input_loop_task.cancel()
            update_rank_task.cancel()
            for t in workers:
                t.cancel()
            return
