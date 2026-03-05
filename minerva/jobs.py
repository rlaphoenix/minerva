import asyncio
import os
import urllib.parse
from typing import Any

import httpx
import humanize
import websockets
from websockets.connection import Connection

from minerva.cache import job_cache
from minerva.console import WorkerDisplay
from minerva.constants import RETRY_DELAY, SUBCHUNK_SIZE, USER_AGENT
from minerva.ws_message import WSMessage, WSMessageType


async def process_job(
    job: dict,
    server: Connection,
    retries: int,
    display: WorkerDisplay,
    lock: asyncio.Lock,
    stop: asyncio.Event,
    job_response_futures: dict[str, asyncio.Future],
    job_response_lock: asyncio.Lock,
) -> None:
    label = urllib.parse.unquote(os.path.basename(job["url"]))
    chunk_size: int = (job["range"][1] - job["range"][0]) + 1  # range is inclusive, so add 1 to get size
    last_err: Exception | None = None
    job_cache.set(job)
    display.job_start(job, label)

    for attempt in range(1, retries + 1):
        display.job_update(job["file_id"], "OK", size=chunk_size, downloaded=0, uploaded=0, waiting=False)
        downloaded = 0
        uploaded = 0
        async with httpx.AsyncClient() as client:
            try:
                async with (
                    client.stream(
                        method="GET",
                        url=job["url"],
                        headers={
                            "User-Agent": USER_AGENT,
                            "Range": f"bytes={job['range'][0]}-{job['range'][1] - 1}",  # -1 because it seems range is inclusive
                        },
                        follow_redirects=True,
                    ) as response
                ):
                    response.raise_for_status()
                    async for data_chunk in response.aiter_bytes(SUBCHUNK_SIZE):
                        # TODO: maybe make part of progress bar blue for dl, green for ul
                        if stop.is_set():
                            display.job_done(job["file_id"], label, ok=False, note="Stopping...")
                            return

                        downloaded += len(data_chunk)
                        display.job_update(
                            file_id=job["file_id"], status="OK", size=chunk_size, downloaded=downloaded, uploaded=uploaded, waiting=False
                        )

                        # TODO: cannpt handle multiple concurrent uploads because server responses do not have an identifier
                        # future = asyncio.get_running_loop().create_future()
                        # async with job_response_lock:
                        #     job_response_futures[job["chunk_id"]] = future
                        # async with lock:
                        #     await server.send(
                        #         WSMessage(
                        #             WSMessageType.UPLOAD_SUBCHUNK,
                        #             {"chunk_id": job["chunk_id"], "file_id": job["file_id"], "payload": data_chunk},
                        #         ).encode()
                        #     )
                        # ws_response: WSMessage = await future

                        future = asyncio.get_running_loop().create_future()
                        async with lock:
                            async with job_response_lock:
                                job_response_futures[job["chunk_id"]] = future
                            await server.send(
                                WSMessage(
                                    WSMessageType.UPLOAD_SUBCHUNK,
                                    {"chunk_id": job["chunk_id"], "file_id": job["file_id"], "payload": data_chunk},
                                ).encode()
                            )
                            ws_response: WSMessage = await future

                        payload = ws_response.get_payload()
                        if not isinstance(payload, dict):
                            raise Exception(f"Unexpected response payload ({type(payload)}): {payload}")
                        is_downloaded = (downloaded + len(data_chunk)) >= chunk_size
                        is_error = ws_response.get_type() != WSMessageType.OK_RESPONSE
                        if is_downloaded and payload.get("error") in ["Chunk already complete", "Unknown chunk"]:
                            # TODO: these seem to happen once a file gets uploaded, needs to be double checked
                            is_error = False
                        if is_error:
                            await report_job_failure(job, server, lock, job_response_futures, job_response_lock)
                            raise Exception(f"Bad response from server: {payload}")

                        uploaded += len(data_chunk)
                        display.job_update(
                            file_id=job["file_id"],
                            status="OK",
                            size=chunk_size,
                            downloaded=downloaded,
                            uploaded=uploaded,
                            waiting=False,
                        )
            except websockets.exceptions.WebSocketException as e:
                print(f"Websocket error while processing job {job['file_id']}: {e}")
                last_err = e
                if attempt < retries:
                    display.job_update(job["file_id"], "RT", downloaded=0, uploaded=0, waiting=True)
                    await asyncio.sleep(RETRY_DELAY * attempt)
                else:
                    display.job_done(
                        job["file_id"], label, ok=False, note=f"Job Failed ({retries} attempts): {str(last_err)}"
                    )
            except Exception as e:
                print(f"Error while processing job {job['file_id']}: {e}")
                last_err = e
                if attempt < retries:
                    display.job_update(job["file_id"], "RT", downloaded=0, uploaded=0, waiting=True)
                    await asyncio.sleep(RETRY_DELAY * attempt)
                else:
                    display.job_done(
                        job["file_id"], label, ok=False, note=f"Job Failed ({retries} attempts): {str(last_err)}"
                    )
                    await report_job_failure(job, server, lock, job_response_futures, job_response_lock)

    display.job_done(job["file_id"], label, ok=True, note=humanize.naturalsize(chunk_size) if chunk_size else "")


async def report_job_failure(
    job: dict[str, Any],
    server: Connection,
    lock: asyncio.Lock,
    job_response_futures: dict[str, asyncio.Future],
    job_response_lock: asyncio.Lock,
) -> None:
    try:
        future = asyncio.get_running_loop().create_future()
        async with job_response_lock:
            job_response_futures[job["chunk_id"]] = future
        async with lock:
            await server.send(WSMessage(WSMessageType.DETACH_CHUNK, {"chunk_id": job["chunk_id"]}).encode())
        await future  # absorb unwanted response
    except Exception:
        pass
    await asyncio.sleep(5)
