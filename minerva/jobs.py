import asyncio
import logging
import os
import urllib.parse

import httpx
import humanize
import websockets
from websockets.connection import Connection

from minerva.console import WorkerDisplay
from minerva.constants import RETRY_DELAY, SUBCHUNK_SIZE, USER_AGENT
from minerva.ws_message import (
    ChunkInfo,
    ErrorResponseMessage,
    OkResponseMessage,
    UploadSubchunkMessage,
    WSMessage,
    WSMessageType,
)


async def process_job(
    job: ChunkInfo,
    server: Connection,
    worker_id: str,
    retries: int,
    display: WorkerDisplay,
    lock: asyncio.Lock,
    reconnect_event: asyncio.Event,
    ctrl_c_event: asyncio.Event,
    websocket_futures: dict[str, asyncio.Future],
    websocket_futures_lock: asyncio.Lock,
) -> None:
    if ctrl_c_event.is_set() or reconnect_event.is_set():
        return

    log = logging.getLogger(__file__)
    label = urllib.parse.unquote(os.path.basename(job.url))
    await display.job_start(job, label, worker_id)

    chunk_size: int = job.end - job.start  # range is inclusive
    for attempt in range(1, retries + 1):
        if ctrl_c_event.is_set() or reconnect_event.is_set():
            await display.job_done(job.file_id, label, ok=False, note="Stopping...")
            return
        await display.job_update(job.file_id, "OK", size=chunk_size, downloaded=0, uploaded=0, waiting=False)
        downloaded = 0
        uploaded = 0
        async with httpx.AsyncClient() as client:
            try:
                async with client.stream(
                    method="GET",
                    url=job.url,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Range": f"bytes={job.start}-{job.end - 1}",  # -1 because it seems range is inclusive
                    },
                    follow_redirects=True,
                ) as response:
                    response.raise_for_status()
                    async for data_chunk in response.aiter_bytes(SUBCHUNK_SIZE):
                        if ctrl_c_event.is_set() or reconnect_event.is_set():
                            raise websockets.exceptions.WebSocketException("Stop event set, stopping job")

                        downloaded += len(data_chunk)
                        await display.job_update(
                            file_id=job.file_id,
                            status="OK",
                            size=chunk_size,
                            downloaded=downloaded,
                            uploaded=uploaded,
                            waiting=False,
                        )

                        future = asyncio.get_running_loop().create_future()
                        async with websocket_futures_lock:
                            websocket_futures[job.chunk_id] = future
                        async with lock:
                            await server.send(
                                UploadSubchunkMessage(
                                    chunk_id=job.chunk_id,
                                    file_id=job.file_id,
                                    payload=data_chunk,
                                ).encode()
                            )
                        ws_response: WSMessage = await future

                        if isinstance(ws_response, ErrorResponseMessage):
                            raise Exception(ws_response.values["error"])
                        if not isinstance(ws_response, OkResponseMessage):
                            raise Exception(f"Unexpected response type: {type(ws_response)}")

                        uploaded += len(data_chunk)
                        await display.job_update(
                            file_id=job.file_id,
                            status="OK",
                            size=chunk_size,
                            downloaded=downloaded,
                            uploaded=uploaded,
                            waiting=False,
                        )
                    await display.job_done(
                        job.file_id, label, ok=True, note=humanize.naturalsize(chunk_size) if chunk_size else ""
                    )
                    break
            except (websockets.exceptions.WebSocketException, asyncio.CancelledError):
                # we cannot keep downloading with a lost websocket connection
                # signal that we need to reconnect, loop, and let the code above mark as fail
                reconnect_event.set()
            except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 404:
                    await display.job_done(job.file_id, label, ok=False, note="Not Found (404)")
                    return
                if attempt < retries:
                    await display.job_update(job.file_id, "RT", downloaded=0, uploaded=0, waiting=True)
                    if ctrl_c_event.is_set() or reconnect_event.is_set():
                        continue
                    await asyncio.sleep(RETRY_DELAY * attempt)
                else:
                    await display.job_done(
                        job.file_id, label, ok=False, note=f"Job Failed ({retries} attempts, httpx error): {str(e)}"
                    )
                    if ctrl_c_event.is_set() or reconnect_event.is_set():
                        continue
                    await report_job_failure(job, server, lock, websocket_futures, websocket_futures_lock)
            except (Exception, BaseException) as e:
                log.exception(f"Unexpected exception while processing job {job.file_id}: {e}", exc_info=True)
                if attempt < retries:
                    await display.job_update(job.file_id, "RT", downloaded=0, uploaded=0, waiting=True)
                    if ctrl_c_event.is_set() or reconnect_event.is_set():
                        continue
                    await asyncio.sleep(RETRY_DELAY * attempt)
                else:
                    await display.job_done(
                        job.file_id,
                        label,
                        ok=False,
                        note=f"Job Failed ({retries} attempts, unexpected error): {str(e)}",
                    )
                    if ctrl_c_event.is_set() or reconnect_event.is_set():
                        continue
                    await report_job_failure(job, server, lock, websocket_futures, websocket_futures_lock)


async def report_job_failure(
    job: ChunkInfo,
    server: Connection,
    lock: asyncio.Lock,
    websocket_futures: dict[str, asyncio.Future],
    websocket_futures_lock: asyncio.Lock,
) -> None:
    try:
        future = asyncio.get_running_loop().create_future()
        async with websocket_futures_lock:
            websocket_futures[job.chunk_id] = future
        async with lock:
            await server.send(WSMessage(WSMessageType.DETACH_CHUNK, {"chunk_id": job.chunk_id}).encode())
        await future  # absorb unwanted response
    except Exception:
        pass
    await asyncio.sleep(5)
