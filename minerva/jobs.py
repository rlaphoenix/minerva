import asyncio
import os
import urllib.parse

import httpx
import humanize
import websockets
from websockets.connection import Connection

from minerva.console import WorkerDisplay, console
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
    retries: int,
    display: WorkerDisplay,
    lock: asyncio.Lock,
    stop: asyncio.Event,
    job_response_futures: dict[str, asyncio.Future],
    job_response_lock: asyncio.Lock,
) -> None:
    label = urllib.parse.unquote(os.path.basename(job.url))
    chunk_size: int = job.end - job.start  # range is inclusive
    last_err: Exception | None = None

    display.job_start(job, label)
    for attempt in range(1, retries + 1):
        display.job_update(job.file_id, "OK", size=chunk_size, downloaded=0, uploaded=0, waiting=False)
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
                        if stop.is_set():
                            display.job_done(job.file_id, label, ok=False, note="Stopping...")
                            return

                        downloaded += len(data_chunk)
                        display.job_update(
                            file_id=job.file_id,
                            status="OK",
                            size=chunk_size,
                            downloaded=downloaded,
                            uploaded=uploaded,
                            waiting=False,
                        )

                        future = asyncio.get_running_loop().create_future()
                        async with job_response_lock:
                            job_response_futures[job.chunk_id] = future
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
                        display.job_update(
                            file_id=job.file_id,
                            status="OK",
                            size=chunk_size,
                            downloaded=downloaded,
                            uploaded=uploaded,
                            waiting=False,
                        )
                        break
            except websockets.exceptions.WebSocketException as e:
                print(f"Websocket error while processing job {job.file_id}: {e}")
                last_err = e
                if attempt < retries:
                    display.job_update(job.file_id, "RT", downloaded=0, uploaded=0, waiting=True)
                    await asyncio.sleep(RETRY_DELAY * attempt)
                else:
                    display.job_done(
                        job.file_id, label, ok=False, note=f"Job Failed ({retries} attempts): {str(last_err)}"
                    )
            except TimeoutError as e:
                last_err = e
                if attempt < retries:
                    display.job_update(job.file_id, "RT", downloaded=0, uploaded=0, waiting=True)
                    await asyncio.sleep(RETRY_DELAY * attempt)
                else:
                    display.job_done(
                        job.file_id, label, ok=False, note=f"Job Failed ({retries} attempts): {str(last_err)}"
                    )
                    await report_job_failure(job, server, lock, job_response_futures, job_response_lock)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    display.job_done(job.file_id, label, ok=False, note="Not Found (404)")
                    return
                print(f"HTTP error while processing job {job.file_id}: {e}")
                last_err = e
                if attempt < retries:
                    display.job_update(job.file_id, "RT", downloaded=0, uploaded=0, waiting=True)
                    await asyncio.sleep(RETRY_DELAY * attempt)
                else:
                    display.job_done(
                        job.file_id, label, ok=False, note=f"Job Failed ({retries} attempts): {str(last_err)}"
                    )
                    await report_job_failure(job, server, lock, job_response_futures, job_response_lock)
            except Exception as e:
                if str(e):
                    print(f"Error while processing job {job.file_id}: {e}")
                else:
                    console.print_exception()
                last_err = e
                if attempt < retries:
                    display.job_update(job.file_id, "RT", downloaded=0, uploaded=0, waiting=True)
                    await asyncio.sleep(RETRY_DELAY * attempt)
                else:
                    display.job_done(
                        job.file_id, label, ok=False, note=f"Job Failed ({retries} attempts): {str(last_err)}"
                    )
                    await report_job_failure(job, server, lock, job_response_futures, job_response_lock)

    display.job_done(job.file_id, label, ok=True, note=humanize.naturalsize(chunk_size) if chunk_size else "")


async def report_job_failure(
    job: ChunkInfo,
    server: Connection,
    lock: asyncio.Lock,
    job_response_futures: dict[str, asyncio.Future],
    job_response_lock: asyncio.Lock,
) -> None:
    try:
        future = asyncio.get_running_loop().create_future()
        async with job_response_lock:
            job_response_futures[job.chunk_id] = future
        async with lock:
            await server.send(WSMessage(WSMessageType.DETACH_CHUNK, {"chunk_id": job.chunk_id}).encode())
        await future  # absorb unwanted response
    except Exception:
        pass
    await asyncio.sleep(5)
