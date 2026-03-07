import asyncio
import logging
import os
import sys
import time
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
    WSMessage,
    decode_message,
)


async def input_loop(display: WorkerDisplay) -> None:
    import readchar

    while True:
        key = await asyncio.to_thread(readchar.readkey)

        async with display._lock:
            if key == readchar.key.RIGHT:
                display._page += 1
            elif key == readchar.key.LEFT:
                display._page -= 1


async def update_rank_loop(display: WorkerDisplay, server: str) -> None:
    log = logging.getLogger(__file__)
    while True:
        try:
            await display.update_rank(server)
        except Exception:
            log.exception("Error updating rank", exc_info=True)
        await asyncio.sleep(10)


async def worker_loop(
    token: str,
    server: str,
    concurrency: int,
    retries: int,
    min_job_size: str,
    max_job_size: str,
) -> None:
    log = logging.getLogger(__file__)

    # prevent "too many open files" errors with high concurrency
    if os.name == "posix":
        os.system("ulimit -n 16384")

    # compute optimal concurrency based on download speed tests
    if not concurrency:
        log.info("Testing raw download speed...")
        download_speed = await test_download_speed(SPEED_TEST_URL)
        log.info(f"Result: {naturalsize(download_speed)}/s[/dim]")
        log.info("Testing download speed from Myrient...")
        myrient_download_speed = await test_download_speed(MYRIENT_SPEED_TEST_URL)
        log.info(f"Result: {naturalsize(myrient_download_speed)}/s[/dim]")
        concurrency = int(download_speed // myrient_download_speed)
    concurrency = max(1, min(concurrency, MAX_CHUNK_COUNT))

    # display configuration
    log.info(f"Server URL:     [dim]{server}[/dim]")
    log.info(f"Concurrency:    [dim]{concurrency}[/dim]")
    log.info(f"Retries:        [dim]{retries}[/dim]")
    log.info(f"Min job size:   [dim]{naturalsize(parse_size(min_job_size)) if min_job_size else 'N/A'}[/dim]")
    log.info(f"Max job size:   [dim]{naturalsize(parse_size(max_job_size)) if max_job_size else 'N/A'}[/dim]")
    log.info("")

    # ui and configuration
    display = WorkerDisplay()
    min_job_size_bytes = parse_size(min_job_size) if min_job_size else None
    max_job_size_bytes = parse_size(max_job_size) if max_job_size else None

    # where jobs are stored before being processed
    job_queue: dict[str, asyncio.Queue] = {}
    active_job_ids: set[str] = set()  # used to prevent multiple workers from processing the same job
    active_job_ids_lock: asyncio.Lock = asyncio.Lock()  # protects the active_job_ids set

    # awaitables for websocket responses, keyed by either worker_id or chunk_id depending on the message type
    websocket_futures: dict[str, asyncio.Future] = {}
    websocket_futures_lock: asyncio.Lock = asyncio.Lock()
    websocket_lock: asyncio.Lock = asyncio.Lock()  # TODO: Do we need both locks separate? can we reuse?

    # used to signal the producer to stop fetching new jobs or the workers to stop processing jobs
    ctrl_c_event: asyncio.Event = asyncio.Event()  # used when we want a graceful shutdown
    reconnect_event: dict[str, asyncio.Event] = {}  # used when we need to reconnect to the server

    # server connection and server assigned worker id
    connection: ClientConnection | None = None
    worker_id: str | None = None

    async def queue_jobs(jobs: list[ChunkInfo], worker_id: str) -> int:
        """
        Add a job to the queue.

        Skips active jobs that are already being processed or queued.
        Also skips jobs that do not meet the size requirements.

        Returns the number of jobs added to the queue.
        """
        jobs_queued = 0
        for job in jobs:
            async with active_job_ids_lock:
                if job.file_id in active_job_ids:
                    continue
                active_job_ids.add(job.file_id)

            size = job.end - job.start
            if size:
                filename = Path(urlparse(unquote(job.url)).path).name
                if min_job_size_bytes and (size < min_job_size_bytes):
                    log.warning(
                        f"[yellow]Skipping job {filename} "
                        f"({naturalsize(size)} < "
                        f"{naturalsize(min_job_size_bytes)})[/yellow]"
                    )
                    continue
                if max_job_size_bytes and (size > max_job_size_bytes):
                    log.warning(
                        f"[yellow]Skipping job {filename} "
                        f"({naturalsize(size)} > "
                        f"{naturalsize(max_job_size_bytes)})[/yellow]"
                    )
                    continue

            await job_queue[worker_id].put(job)
            jobs_queued += 1

        return jobs_queued

    async def producer(websocket: ClientConnection, worker_id: str) -> int:
        """
        Gets jobs from the server where needed and adds them to the queue.
        Skips fetching new jobs if the stop event is set.

        To prevent overhwleming the worker processor and server, new jobs are delayed if:
        - the queue is more than half full
        - there were no free slots in the queue
        - the server doesn't have any jobs available
        - an error occurs while requesting jobs from the server
        - all jobs received from the server were skipped by filters

        Returns the amount of total jobs produced in it's lifetime.
        Only returns if the producer loop exits gracefully, otherwise it runs indefinitely until cancelled.
        """
        jobs_produced = 0

        try:
            while not ctrl_c_event.is_set() and not reconnect_event[worker_id].is_set():
                # if queue is more than half full, backoff for 1s
                if job_queue[worker_id].qsize() >= max(1, job_queue[worker_id].maxsize // 2):
                    await asyncio.sleep(1)
                    continue

                free_slots = max(0, concurrency - job_queue[worker_id].qsize())
                fetch_count = max(0, min(concurrency, free_slots))
                if fetch_count <= 0:
                    log.info("Job queue is full, waiting 2s...")
                    await asyncio.sleep(2)
                    continue

                try:
                    log.info("Requesting %d jobs from the server...", fetch_count)
                    future = asyncio.get_running_loop().create_future()
                    async with websocket_futures_lock:
                        websocket_futures[worker_id] = future
                    async with websocket_lock:
                        if reconnect_event[worker_id].is_set():
                            break
                        await websocket.send(GetChunksMessage(count=fetch_count).encode())
                    ws_response: WSMessage = await asyncio.wait_for(future, timeout=30)

                    if isinstance(ws_response, ErrorResponseMessage):
                        raise Exception(ws_response.values["error"])
                    if not isinstance(ws_response, ChunkResponseMessage):
                        raise Exception(f"Unexpected response type: {type(ws_response)}")
                    if ws_response.chunks:
                        jobs_queued = await queue_jobs(ws_response.chunks, worker_id)
                        log.info(
                            "Queued %d jobs, there are now %d jobs in the queue",
                            jobs_queued,
                            job_queue[worker_id].qsize(),
                        )
                        jobs_produced += jobs_queued
                        if jobs_queued == 0:
                            log.warning("Received %d jobs, but all were skipped by filters", len(ws_response.chunks))
                            await asyncio.sleep(5)
                            continue
                    else:
                        if job_queue[worker_id].qsize() == 0:
                            log.warning("Server currently has no jobs available...")
                        await asyncio.sleep(RETRY_DELAY)
                        continue
                except Exception as e:
                    log.error("Failed requesting more jobs %s (%s)", worker_id, e)
                    await asyncio.sleep(RETRY_DELAY)
                    continue
        except websockets.exceptions.WebSocketException:
            log.error("Producer loop %s had a WebSocket error, exiting...", worker_id)
        except asyncio.CancelledError:
            pass  # log.info("Producer loop %s received cancellation, exiting...", worker_id)

        return jobs_produced

    async def worker(websocket: ClientConnection, worker_id: str) -> tuple[int, int]:
        """
        Worker thread, takes jobs from the queue and processes them.

        If an error occurs while processing a job, the job is marked as failed
        and the loop continues with the next job. The failed job's ID will be
        removed from the active job set, allowing it to be re-queued in the future.

        To prevent overhwleming the worker processor and server, processing jobs are delayed if:
        - an error occurs while processing jobs from the queue

        Returns the amount of successful and failed jobs processed in it's lifetime.
        """
        jobs_success = 0
        jobs_fail = 0

        try:
            while not ctrl_c_event.is_set() and not reconnect_event[worker_id].is_set():
                job: ChunkInfo = await job_queue[worker_id].get()
                try:
                    await process_job(
                        job=job,
                        server=websocket,
                        worker_id=worker_id,
                        retries=retries,
                        display=display,
                        lock=websocket_lock,
                        reconnect_event=reconnect_event[worker_id],
                        ctrl_c_event=ctrl_c_event,
                        websocket_futures=websocket_futures,
                        websocket_futures_lock=websocket_futures_lock,
                    )
                    jobs_success += 1
                except Exception as e:
                    jobs_fail += 1
                    log.error("[worker] Error processing job %s: %s", job.file_id, e)
                    await asyncio.sleep(RETRY_DELAY)
                    continue
                finally:
                    async with active_job_ids_lock:
                        active_job_ids.discard(job.file_id)
                    job_queue[worker_id].task_done()
        except websockets.exceptions.WebSocketException:
            log.error("[worker] Worker loop %s had a WebSocket error, exiting...", worker_id)
        except asyncio.CancelledError:
            pass  # log.info("[worker] Worker loop %s received cancellation, exiting...", worker_id)

        return jobs_success, jobs_fail

    async def clear_job_queue(worker_id: str) -> None:
        return
        try:
            while True:
                job_queue[worker_id].get_nowait()
                job_queue[worker_id].task_done()
        except asyncio.QueueEmpty:
            pass

    async def stop_jobs(worker_id: str | None = None) -> None:
        # signal currently running jobs to abort
        if worker_id:
            reconnect_event[worker_id].set()
            await display.remove_jobs(worker_id)
            await clear_job_queue(worker_id)
        # flush websocket futures so they don't try to write to a dead websocket
        async with websocket_futures_lock:
            try:
                while True:
                    future = websocket_futures.popitem()[1]
                    if future and not future.done():
                        future.cancel()
                    else:
                        break
            except KeyError:
                pass
        # reset what jobs are active since they should all be stopped now
        async with active_job_ids_lock:
            active_job_ids.clear()

    async def websocket_receiver(
        websocket: ClientConnection,
        worker_id: str,
        websocket_futures: dict[str, asyncio.Future],
        websocket_futures_lock: asyncio.Lock,
    ) -> None:
        """
        Receives messages from the websocket and processes them accordingly.

        This function runs in an infinite loop, decoding messages received from the
        websocket and handling them based on their type. It updates the appropriate
        queues and futures, and handles websocket closure and errors.

        Args:
            websocket (ClientConnection): The websocket connection to receive messages from.
            websocket_futures (dict[str, asyncio.Future]): Dictionary mapping chunk IDs to futures.
            websocket_futures_lock (asyncio.Lock): Lock to synchronize access to websocket_futures.

        Returns:
            None
        """
        try:
            while not ctrl_c_event.is_set() and not reconnect_event[worker_id].is_set():
                raw = await asyncio.wait_for(websocket.recv(), timeout=CONNECTIVITY_CHECK_TIMEOUT * 10)
                msg = decode_message(raw)

                future_key: str
                if isinstance(msg, ChunkResponseMessage):
                    future_key = worker_id
                elif isinstance(msg, (ErrorResponseMessage, OkResponseMessage)):
                    if not msg.values.get("chunk_id"):
                        log.warning(f"Received a ({type(msg)}) message without correlation ID: {msg}")
                        continue
                    future_key = msg.values["chunk_id"]
                else:
                    log.warning(f"Received unrecognized message type: {msg.get_type()}")
                    continue

                async with websocket_futures_lock:
                    future = websocket_futures.pop(future_key, None)

                if future and not future.done():
                    future.set_result(msg)
        except websockets.ConnectionClosed as e:
            log.error("Websocket connection closed during use in receiver: %s", str(e))
            await stop_jobs(worker_id)
            log.warning("All files should DEFINITELY be stopped now")
        except Exception:
            log.exception("An unexpected error occurred")
            raise

    # start the update rank task
    update_rank_task = asyncio.create_task(update_rank_loop(display, server))

    # start the input loop if stdin is a TTY
    input_loop_task = None
    if sys.stdin.isatty():
        input_loop_task = asyncio.create_task(input_loop(display))

    with Live(display, console=console, refresh_per_second=4, screen=False):
        while True:
            # 1. Server died, start fresh with no jobs, running jobs should stop as failures
            await stop_jobs()
            display.connected = False
            if display.downtime == 0:
                display.downtime = time.monotonic()

            # 2. Try to connect to the WebSocket server, if it fails, wait 10s and try again
            log.info("Trying to connect to the coordinator server...")
            try:
                connection = await websockets.connect(
                    f"wss://{server.replace('https://', '')}{WORKER_ENDPOINT}",
                    open_timeout=CONNECTIVITY_CHECK_TIMEOUT * 3,
                    ping_interval=CONNECTIVITY_CHECK_TIMEOUT * 5,
                    ping_timeout=CONNECTIVITY_CHECK_TIMEOUT * 5,
                )
                if not connection:
                    raise ValueError("connection object empty")
            except Exception as e:
                log.error("Failed to connect to the server, trying again in %d seconds... (%s)", RETRY_DELAY, e)
                await asyncio.sleep(RETRY_DELAY)
                continue
            log.info("Successfully connected to the Server")

            async with connection as websocket:
                # 3. Register with the Server Coordinator service
                try:
                    async with websocket_lock:
                        await websocket.send(
                            RegisterMessage(
                                version=SERVER_VERSION, max_concurrent=concurrency, access_token=token
                            ).encode()
                        )
                        response = decode_message(await websocket.recv())
                    if isinstance(response, ErrorResponseMessage):
                        raise Exception(response.values["error"])
                    if not isinstance(response, RegisterResponseMessage):
                        raise Exception(f"Unexpected response type: {type(response)}")
                    worker_id = response.worker_id
                    if not worker_id:
                        raise ValueError("worker_id empty")
                except Exception as e:
                    log.error("Error: Failed to register on server, trying again in %d seconds... (%s)", RETRY_DELAY, e)
                    await asyncio.sleep(RETRY_DELAY)
                    continue
                log.info("Registered with the Server Coordinator Service")

                # initialize the reconnect event for this worker, mark as connected
                job_queue[worker_id] = asyncio.Queue(maxsize=concurrency)
                reconnect_event[worker_id] = asyncio.Event()
                display.connected = True
                display.downtime = 0.0

                # 4. Setup the producer, workers, websocket receiver, and rank updater
                producer_task = asyncio.create_task(producer(websocket, worker_id))
                receiver_task = asyncio.create_task(
                    websocket_receiver(
                        websocket=websocket,
                        worker_id=worker_id,
                        websocket_futures=websocket_futures,
                        websocket_futures_lock=websocket_futures_lock,
                    )
                )
                workers = [asyncio.create_task(worker(websocket, worker_id)) for _ in range(concurrency)]
                tasks = [*workers, producer_task, receiver_task]

                # 5. Wait for any of the workers, producer, or receiver to error out or finish
                try:
                    # once any of the tasks have an exception, cancel all of them
                    _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=False)
                    log.warning("All workers and tasks exited, restarting...")
                    await stop_jobs(worker_id)
                except KeyboardInterrupt:
                    log.info("Received Ctrl+C, shutting down...")
                    # signal all loops to stop, we want to fully shut down the worker, not reconnect
                    ctrl_c_event.set()
                    log.info("Shutting down…")
                    # cancell all workers, producer, and receiver tasks
                    for task in tasks:
                        task.cancel()
                    # stop the rank update loop
                    update_rank_task.cancel()
                    # stop the input loop if it was started
                    if input_loop_task:
                        input_loop_task.cancel()
                    # wait for all jobs in queue to finish
                    await job_queue[worker_id].join()
                    # wait for all workers, the producer, and the receiver to exit
                    await asyncio.gather(*tasks, return_exceptions=True)
                    # don't start up again
                    return
                except Exception as e:
                    log.exception(f"Error in a task: {e}", exc_info=True)
                    await stop_jobs(worker_id)
                    await asyncio.sleep(RETRY_DELAY)
                    continue
