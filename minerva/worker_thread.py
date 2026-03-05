import os
import urllib.parse
from threading import Lock, Thread

import httpx
import websockets
from rich.progress import Progress
from websockets import ClientConnection

from minerva.constants import USER_AGENT
from minerva.ws_message import WSMessage, WSMessageType


class WorkerThread:
    def __init__(
        self,
        chunk_id: str,
        file_id: str,
        url: str,
        range_start: int,
        range_end: int,
        websocket: ClientConnection,
        websocket_lock: Lock,
        user_agent: str,
        subchunk_size: int,
        progress_bar: Progress,
        task_id: int,
    ):
        self.chunk_id = chunk_id
        self.file_id = file_id
        self.url = url
        self.range_start = range_start
        self.range_end = range_end
        self.websocket = websocket
        self.websocket_lock = websocket_lock
        self.user_agent = user_agent
        self.progress_bar = progress_bar
        self.task_id = task_id
        self.should_run = True
        self.thread = Thread(target=self.worker_thread)
        self.subchunk_size = subchunk_size
        self.websocket_failed = False

    def get_websocket_failed(self) -> bool:
        return self.websocket_failed

    def is_alive(self) -> bool:
        return self.thread.is_alive()

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.should_run = False

    def worker_thread(self) -> None:
        try:
            with httpx.stream(
                method="GET",
                url=self.url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Range": f"bytes={self.range_start}-{self.range_end - 1}",  # -1 because it seems range is inclusive
                },
            ) as response:
                for chunk in response.iter_bytes(self.subchunk_size):
                    if not self.should_run:
                        self.progress_bar.stop_task(self.task_id)
                        return
                    with self.websocket_lock:
                        self.websocket.send(
                            WSMessage(
                                WSMessageType.UPLOAD_SUBCHUNK,
                                {"chunk_id": self.chunk_id, "file_id": self.file_id, "payload": chunk},
                            ).encode()
                        )
                        ws_response = WSMessage.decode(self.websocket.recv())
                    if ws_response.get_type() != WSMessageType.OK_RESPONSE:
                        self.progress_bar.log(
                            f"[ERR]: Could not upload {urllib.parse.unquote(os.path.basename(self.url))}"
                        )
                        ws_response.get_payload()
                        self.progress_bar.stop_task(self.task_id)
                        with self.websocket_lock:
                            self.websocket.send(
                                WSMessage(WSMessageType.DETACH_CHUNK, {"chunk_id": self.chunk_id}).encode()
                            )
                            ws_response = WSMessage.decode(self.websocket.recv())  # just wait for the next message
                        return
                    self.progress_bar.update(self.task_id, advance=len(chunk))
        except websockets.exceptions.WebSocketException as e:
            self.websocket_failed = True
            self.should_run = False
            self.progress_bar.log(f"[ERR]: Websocket connection had an error: {e}")
        except Exception as e:
            self.progress_bar.log(f"[ERR]: Could not download {urllib.parse.unquote(os.path.basename(self.url))}: {e}")
            try:
                with self.websocket_lock:
                    self.websocket.send(WSMessage(WSMessageType.DETACH_CHUNK, {"chunk_id": self.chunk_id}).encode())
                    ws_response = WSMessage.decode(self.websocket.recv())  # just wait for the next message
            except Exception as e:
                self.websocket_failed = True
                self.should_run = False
                self.progress_bar.log(f"[ERR]: Failed to detach chunk: {e}")
        self.progress_bar.stop_task(self.task_id)
