import asyncio
from pathlib import Path
from random import random

import httpx
import jwt
from rich.live import Live

from minerva.auth import auth_headers
from minerva.console import WorkerDisplay, console
from minerva.constants import ARIA2C, MAX_RETRIES, QUEUE_PREFETCH
from minerva.error_handling import _raise_if_upgrade_required
from minerva.jobs import process_job

_STOP = object()


async def worker_loop(
    server_url: str,
    upload_server_url: str,
    token: str,
    temp_dir: Path,
    concurrency: int,
    batch_size: int,
    aria2c_connections: int,
    pre_allocation: str,
    keep_files: bool,
) -> None:
    token_dec = jwt.decode(token, options={"verify_signature": False})

    console.print(f"Username:      {token_dec.get('username', '-')}")
    console.print(f"Job Gateway:   {server_url}")
    console.print(f"Upload Server: {upload_server_url}")
    console.print(f"Concurrency:   {concurrency}")
    console.print(f"Retries:       {MAX_RETRIES}")
    console.print(f"Keep files:    {'yes' if keep_files else 'no'}")
    console.print(
        f"Downloader:    {f'aria2c ({aria2c_connections} conns/job)' if ARIA2C else 'httpx'}"
    )
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
    display = WorkerDisplay()

    # ── Producer ────────────────────────────────────────────────────────────
    async def producer() -> None:
        no_jobs_warned = False
        async with httpx.AsyncClient(timeout=30) as api:
            while not stop_event.is_set():
                if queue.qsize() >= concurrency:
                    await asyncio.sleep(0.5)
                    continue

                free_slots = max(1, queue.maxsize - queue.qsize())
                fetch_count = min(4, batch_size, free_slots)
                try:
                    resp = await api.get(
                        f"{server_url}/api/jobs",
                        params={"count": fetch_count},
                        headers=auth_headers(token),
                    )
                    if resp.status_code == 426:
                        _raise_if_upgrade_required(resp)
                    if resp.status_code == 401:
                        console.print("[red]Token expired. Run: python worker.py login")
                        stop_event.set()
                        break
                    resp.raise_for_status()
                    jobs = resp.json().get("jobs", [])

                    if not jobs:
                        if not no_jobs_warned:
                            console.print("[dim]No jobs available, waiting 30s…")
                            no_jobs_warned = True
                        await asyncio.sleep(12 + random() * 8)
                        continue

                    no_jobs_warned = False
                    for job in jobs:
                        file_id = job["file_id"]
                        if file_id in seen_ids:
                            continue
                        seen_ids.add(file_id)
                        await queue.put(job)

                except httpx.HTTPError as e:
                    console.print(f"[red]Server error: {e}. Retrying in 10s…")
                    await asyncio.sleep(6 + random() * 4)

        for _ in range(concurrency):
            await queue.put(_STOP)

    # ── Workers ─────────────────────────────────────────────────────────────
    async def worker() -> None:
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
                    aria2c_connections,
                    pre_allocation,
                    display,
                )
            finally:
                seen_ids.discard(job["file_id"])
                queue.task_done()

    with Live(display, console=console, refresh_per_second=4, screen=False):
        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
        producer_task = asyncio.create_task(producer())
        try:
            await asyncio.gather(producer_task, *workers)
        except KeyboardInterrupt:
            console.print("\n[yellow]Shutting down…")
            stop_event.set()
            producer_task.cancel()
            for t in workers:
                t.cancel()
            return
