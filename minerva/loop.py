import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import websockets
from humanfriendly import parse_size
from humanize import naturalsize
from rich.live import Live
from websockets.client import ClientConnection

from minerva.console import WorkerDisplay, console
from minerva.constants import (
    CONNECTIVITY_CHECK_TIMEOUT,
    MAX_CHUNK_COUNT,
    MYRIENT_SPEED_TEST_URL,
    RETRY_DELAY,
    SERVER_VERSION,
    SPEED_TEST_URL,
    WORKER_ENDPOINT,
)
from minerva.jobs import process_job
from minerva.speed import test_download_speed
from minerva.ws_message import (
    ChunkInfo,
    ChunkResponseMessage,
    ErrorResponseMessage,
    GetChunksMessage,
    OkResponseMessage,
    RegisterMessage,
    RegisterResponseMessage,
    decode_message,
)

_STOP = object()


async def input_loop(display: WorkerDisplay) -> None:
    import readchar

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
    token: str,
    server: str,
    concurrency: int,
    retries: int,
    max_cache_size: str,
    min_job_size: str,
    max_job_size: str,
) -> None:
    # prevent "too many open files" errors with high concurrency
    if os.name == "posix":
        os.system("ulimit -n 16384")

    # compute optimal concurrency based on download speed tests
    if not concurrency:
        console.print("Testing raw download speed...")
        download_speed = await test_download_speed(SPEED_TEST_URL)
        console.print(f"[dim] └ Result: {naturalsize(download_speed)}/s[/dim]")
        console.print("Testing download speed from Myrient...")
        myrient_download_speed = await test_download_speed(MYRIENT_SPEED_TEST_URL)
        console.print(f"[dim] └ Result: {naturalsize(myrient_download_speed)}/s[/dim]")
        concurrency = int(download_speed // myrient_download_speed)
    concurrency = max(1, min(concurrency, MAX_CHUNK_COUNT))

    # display configuration
    console.print(f"Server URL:     [dim]{server}[/dim]")
    console.print(f"Concurrency:    [dim]{concurrency}[/dim]")
    console.print(f"Retries:        [dim]{retries}[/dim]")
    console.print(f"Max cache size: [dim]{naturalsize(parse_size(max_cache_size)) if max_cache_size else 'N/A'}[/dim]")
    console.print(f"Min job size:   [dim]{naturalsize(parse_size(min_job_size)) if min_job_size else 'N/A'}[/dim]")
    console.print(f"Max job size:   [dim]{naturalsize(parse_size(max_job_size)) if max_job_size else 'N/A'}[/dim]")
    console.print()

    queue: asyncio.Queue = asyncio.Queue(maxsize=concurrency)
    stop_event = asyncio.Event()
    seen_ids: set[str] = set()
    seen_lock = asyncio.Lock()
    max_queue_size: int = parse_size(max_cache_size) if max_cache_size else 0
    queue_size: int = 0
    display = WorkerDisplay()
    queue_lock = asyncio.Lock()
    websocket_lock = asyncio.Lock()

    cache_available = asyncio.Event()
    cache_available.set()

    min_job_size_bytes = parse_size(min_job_size) if min_job_size else None
    max_job_size_bytes = parse_size(max_job_size) if max_job_size else None

    connection: ClientConnection | None = None
    worker_id: str | None = None
    had_connection = False

    while True:
        console.print("Trying to connect to the coordinator server...")
        try:
            connection = await websockets.connect(
                f"wss://{server.replace('https://', '')}{WORKER_ENDPOINT}",
                open_timeout=CONNECTIVITY_CHECK_TIMEOUT,
                ping_interval=CONNECTIVITY_CHECK_TIMEOUT * 2,
                ping_timeout=CONNECTIVITY_CHECK_TIMEOUT * 2,
            )
            had_connection = True
        except Exception as e:
            if had_connection:
                console.print(f"[red]Lost connection to server, trying again... ({e})[/red]")
            else:
                console.print(f"[red]Failed to connect to the server, trying again... ({e})[/red]")
            await asyncio.sleep(RETRY_DELAY)
            continue

        if not connection:
            raise ValueError("Connection failed or rejected")

        async with connection as websocket:
            chunk_response_queue: asyncio.Queue = asyncio.Queue()
            job_response_futures: dict[str, asyncio.Future] = {}
            job_response_lock = asyncio.Lock()

            # 1. Register with the Hyperscrape Coordinator
            try:
                async with websocket_lock:
                    await websocket.send(
                        RegisterMessage(version=SERVER_VERSION, max_concurrent=concurrency, access_token=token).encode()
                    )
                    response = decode_message(await websocket.recv())
                if isinstance(response, ErrorResponseMessage):
                    raise Exception(response.values["error"])
                if not isinstance(response, RegisterResponseMessage):
                    raise Exception(f"Unexpected response type: {type(response)}")
                worker_id = response.worker_id
            except Exception as e:
                console.print(
                    f"[red]Error: Unable to register with coordinator ({e}), retrying in {RETRY_DELAY}s...[/red]"
                )
                await asyncio.sleep(RETRY_DELAY)
            if not worker_id:
                console.print("[red]COULD NOT CONNECT TO COORDINATOR![/red]")
                console.print("[red]Will try again in one minute...[/red]")
                await asyncio.sleep(60)
            console.print(f"[green]Connected to coordinator with ID: {worker_id}[/green]")

            async def queue_jobs(jobs: list[ChunkInfo]) -> int:
                nonlocal queue_size

                jobs_queued = 0
                for job in jobs:
                    async with seen_lock:
                        if job.file_id in seen_ids:
                            continue
                        seen_ids.add(job.file_id)

                    size = job.end - job.start
                    if size:
                        filename = Path(urlparse(unquote(job.url)).path).name
                        if min_job_size_bytes and (size < min_job_size_bytes):
                            console.print(
                                f"[yellow]Skipping job {filename} "
                                f"({naturalsize(size)} < "
                                f"{naturalsize(min_job_size_bytes)})[/yellow]"
                            )
                            continue
                        if max_job_size_bytes and (size > max_job_size_bytes):
                            console.print(
                                f"[yellow]Skipping job {filename} "
                                f"({naturalsize(size)} > "
                                f"{naturalsize(max_job_size_bytes)})[/yellow]"
                            )
                            continue
                        if max_queue_size:
                            async with queue_lock:
                                if (queue_size + size) > max_queue_size:
                                    console.print(
                                        f"[yellow]Skipping job {filename}  ({max_queue_size} cache size limit)[/yellow]"
                                    )
                                    continue
                                queue_size += size

                    await queue.put(job)
                    jobs_queued += 1

                return jobs_queued

            async def producer() -> None:
                """Producer task to keep the queue filled with jobs from the server."""
                while not stop_event.is_set():
                    async with queue_lock:
                        bloated = max_queue_size and (queue_size >= max_queue_size)
                        free_slots = max(0, queue.maxsize - queue.qsize())

                    if bloated:
                        cache_available.clear()
                        await cache_available.wait()
                        continue

                    if queue.qsize() >= queue.maxsize // 2:
                        await asyncio.sleep(1)
                        continue

                    fetch_count = max(0, min(concurrency, free_slots))
                    if fetch_count > 0:
                        try:
                            async with websocket_lock:
                                await websocket.send(GetChunksMessage(count=fetch_count).encode())
                            response: ChunkResponseMessage = await asyncio.wait_for(
                                chunk_response_queue.get(), timeout=10
                            )
                            if isinstance(response, ErrorResponseMessage):
                                raise Exception(response.values["error"])
                            if not isinstance(response, ChunkResponseMessage):
                                raise Exception(f"Unexpected response type: {type(response)}")
                            if response.chunks:
                                jobs_added = await queue_jobs(response.chunks)
                                if jobs_added == 0:
                                    await asyncio.sleep(5)
                                    continue
                            else:
                                if queue.qsize() == 0:
                                    console.print("[yellow]Server currently has no jobs available...[/yellow]")
                                await asyncio.sleep(RETRY_DELAY)
                                continue
                        except Exception as e:
                            console.print(f"[red]Failed requesting more jobs ({e})[/red]")
                            await asyncio.sleep(RETRY_DELAY)
                            continue
                    else:
                        await asyncio.sleep(2)

                await stop_workers()

            async def worker() -> None:
                """Worker task to process jobs from the queue."""
                nonlocal queue_size
                while True:
                    try:
                        job = await queue.get()
                    except asyncio.CancelledError:
                        return
                    if job is _STOP:
                        queue.task_done()
                        return
                    try:
                        await process_job(
                            job=job,
                            server=websocket,
                            retries=retries,
                            display=display,
                            lock=websocket_lock,
                            stop=stop_event,
                            job_response_futures=job_response_futures,
                            job_response_lock=job_response_lock,
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

            async def stop_workers() -> None:
                for _ in range(concurrency):
                    await queue.put(_STOP)

            async def websocket_receiver(
                websocket: ClientConnection,
                chunk_response_queue: asyncio.Queue,
                job_response_futures: dict[str, asyncio.Future],
                job_response_lock: asyncio.Lock,
            ) -> None:
                try:
                    while True:
                        raw = await websocket.recv()
                        msg = decode_message(raw)

                        msg_type = msg.get_type()
                        if isinstance(msg, ChunkResponseMessage):
                            await chunk_response_queue.put(msg)
                        elif isinstance(msg, ErrorResponseMessage):
                            console.print(f"[red]Error from server: {msg}[/red]")
                            chunk_id = msg.values["chunk_id"]
                            async with job_response_lock:
                                future = job_response_futures.pop(chunk_id, None)
                            if future and not future.done():
                                future.set_result(msg)
                            else:
                                console.print(f"[yellow]Unhandled message type: {msg_type}[/yellow]")
                        elif isinstance(msg, OkResponseMessage):
                            chunk_id = msg.values["chunk_id"]
                            async with job_response_lock:
                                future = job_response_futures.pop(chunk_id, None)
                            if future and not future.done():
                                future.set_result(msg)
                            else:
                                console.print(f"[yellow]Unhandled message type: {msg_type}[/yellow]")
                        else:
                            console.print(f"[yellow]Unhandled message type: {msg_type}[/yellow]")
                except websockets.ConnectionClosed as e:
                    f"[yellow]Websocket closed: code={e.code}, reason={e.reason}[/yellow]"
                    stop_event.set()
                    raise
                except Exception:
                    console.print_exception()
                    raise

            with Live(display, console=console, refresh_per_second=4, screen=False):
                workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
                receiver_task = asyncio.create_task(
                    websocket_receiver(websocket, chunk_response_queue, job_response_futures, job_response_lock)
                )
                producer_task = asyncio.create_task(producer())
                update_rank_task = asyncio.create_task(update_rank_loop(display))
                input_loop_task = None

                if sys.stdin.isatty():
                    input_loop_task = asyncio.create_task(input_loop(display))

                tasks = [receiver_task, producer_task, update_rank_task, *workers]
                if input_loop_task:
                    tasks.append(input_loop_task)

                try:
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                except KeyboardInterrupt:
                    console.print("\n[yellow]Shutting down…[/yellow]")

                    stop_event.set()

                    # stop producer from adding more jobs
                    producer_task.cancel()

                    if input_loop_task:
                        input_loop_task.cancel()

                    update_rank_task.cancel()

                    # wait for all queued jobs to finish
                    await queue.join()

                    # tell workers to exit
                    await stop_workers()

                    # wait for workers to terminate
                    await asyncio.gather(*workers, return_exceptions=True)

                    return
