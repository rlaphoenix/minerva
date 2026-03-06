import struct
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any


def write_u8(buf: BytesIO, value: int) -> None:
    buf.write(struct.pack("<B", value))


def write_u32(buf: BytesIO, value: int) -> None:
    buf.write(struct.pack("<I", value))


def write_u64(buf: BytesIO, value: int) -> None:
    buf.write(struct.pack("<Q", value))


def read_u8(buf: BytesIO) -> int:
    return struct.unpack("<B", buf.read(1))[0]


def read_u32(buf: BytesIO) -> int:
    return struct.unpack("<I", buf.read(4))[0]


def read_u64(buf: BytesIO) -> int:
    return struct.unpack("<Q", buf.read(8))[0]


def write_string(buf: BytesIO, value: str) -> None:
    data = value.encode("utf-8")
    write_u32(buf, len(data))
    buf.write(data)


def read_string(buf: BytesIO) -> str:
    length = read_u32(buf)
    return buf.read(length).decode("utf-8")


def write_bytes(buf: BytesIO, value: bytes) -> None:
    write_u32(buf, len(value))
    buf.write(value)


def read_bytes(buf: BytesIO) -> bytes:
    length = read_u32(buf)
    return buf.read(length)


class WSMessageType:
    REGISTER = 0
    UPLOAD_SUBCHUNK = 1
    GET_CHUNKS = 2
    DETACH_CHUNK = 3

    REGISTER_RESPONSE = 128
    CHUNK_RESPONSE = 129
    ERROR_RESPONSE = 130
    OK_RESPONSE = 131


class WSMessage:
    def __init__(self, type: int, payload: dict[str, Any]):
        self.TYPE = type
        self._payload = payload

    def get_type(self) -> int:
        return self.TYPE

    def encode(self) -> bytes:
        raise NotImplementedError()

    @staticmethod
    def decode(buf: BytesIO) -> "WSMessage":
        raise NotImplementedError()


@dataclass
class RegisterMessage(WSMessage):
    version: int
    max_concurrent: int
    access_token: str

    TYPE = WSMessageType.REGISTER

    def encode(self) -> bytes:
        buf = BytesIO()

        write_u8(buf, self.TYPE)
        write_u32(buf, self.version)
        write_u32(buf, self.max_concurrent)
        write_string(buf, self.access_token)

        return buf.getvalue()

    @classmethod
    def decode(cls, buf: BytesIO) -> "RegisterMessage":
        return cls(version=read_u32(buf), max_concurrent=read_u32(buf), access_token=read_string(buf))


@dataclass
class UploadSubchunkMessage(WSMessage):
    chunk_id: str
    file_id: str
    payload: bytes

    TYPE = WSMessageType.UPLOAD_SUBCHUNK

    def encode(self) -> bytes:
        buf = BytesIO()
        write_u8(buf, self.TYPE)
        write_string(buf, self.chunk_id)
        write_string(buf, self.file_id)
        write_bytes(buf, self.payload)
        return buf.getvalue()

    @classmethod
    def decode(cls, buf: BytesIO) -> "UploadSubchunkMessage":
        return cls(
            chunk_id=read_string(buf),
            file_id=read_string(buf),
            payload=read_bytes(buf),
        )


@dataclass
class GetChunksMessage(WSMessage):
    count: int

    TYPE = WSMessageType.GET_CHUNKS

    def encode(self) -> bytes:
        buf = BytesIO()
        write_u8(buf, self.TYPE)
        write_u32(buf, self.count)
        return buf.getvalue()

    @classmethod
    def decode(cls, buf: BytesIO) -> "GetChunksMessage":
        return cls(count=read_u32(buf))


@dataclass
class DetachChunkMessage(WSMessage):
    chunk_id: str

    TYPE = WSMessageType.DETACH_CHUNK

    def encode(self) -> bytes:
        buf = BytesIO()
        write_u8(buf, self.TYPE)
        write_string(buf, self.chunk_id)
        return buf.getvalue()

    @classmethod
    def decode(cls, buf: BytesIO) -> "DetachChunkMessage":
        return cls(chunk_id=read_string(buf))


@dataclass
class RegisterResponseMessage(WSMessage):
    worker_id: str

    TYPE = WSMessageType.REGISTER_RESPONSE

    def encode(self) -> bytes:
        buf = BytesIO()
        write_u8(buf, self.TYPE)
        write_string(buf, self.worker_id)
        return buf.getvalue()

    @classmethod
    def decode(cls, buf: BytesIO) -> "RegisterResponseMessage":
        return cls(worker_id=read_string(buf))


@dataclass
class ChunkInfo:
    chunk_id: str
    file_id: str
    url: str
    start: int
    end: int


@dataclass
class JobState:
    label: str
    status: str
    size: int
    downloaded: int
    uploaded: int
    waiting: bool
    start_time: float
    prev_done: int
    prev_time: float
    speed: float


@dataclass
class ChunkResponseMessage(WSMessage):
    chunks: list[ChunkInfo]

    TYPE = WSMessageType.CHUNK_RESPONSE

    def encode(self) -> bytes:
        buf = BytesIO()
        write_u8(buf, self.TYPE)
        write_u32(buf, len(self.chunks))
        for chunk in self.chunks:
            write_string(buf, chunk.chunk_id)
            write_string(buf, chunk.file_id)
            write_string(buf, chunk.url)
            write_u64(buf, chunk.start)
            write_u64(buf, chunk.end)
        return buf.getvalue()

    @classmethod
    def decode(cls, buf: BytesIO) -> "ChunkResponseMessage":
        count = read_u32(buf)
        chunks = []
        for _ in range(count):
            chunk_id = read_string(buf)
            file_id = read_string(buf)
            url = read_string(buf)
            start = read_u64(buf)
            end = read_u64(buf)
            chunks.append(ChunkInfo(chunk_id, file_id, url, start, end))
        return cls(chunks=chunks)


@dataclass
class KeyValueResponseMessage(WSMessage):
    TYPE: int = field(default_factory=int, init=False)
    values: dict[str, str] = field(default_factory=dict)

    def encode(self) -> bytes:
        buf = BytesIO()
        write_u8(buf, self.TYPE)
        # write_u32(buf, len(self.values))
        for k, v in self.values.items():
            write_string(buf, k)
            write_string(buf, v)
        return buf.getvalue()

    @classmethod
    def decode(cls, buf: BytesIO) -> "KeyValueResponseMessage":
        count = read_u32(buf)
        values = {}
        for _ in range(count):
            k = read_string(buf)
            v = read_string(buf)
            values[k] = v
        return cls(values=values)


class ErrorResponseMessage(KeyValueResponseMessage):
    TYPE = WSMessageType.ERROR_RESPONSE


class OkResponseMessage(KeyValueResponseMessage):
    TYPE = WSMessageType.OK_RESPONSE


MESSAGE_TYPES = {
    WSMessageType.REGISTER: RegisterMessage,
    WSMessageType.UPLOAD_SUBCHUNK: UploadSubchunkMessage,
    WSMessageType.GET_CHUNKS: GetChunksMessage,
    WSMessageType.DETACH_CHUNK: DetachChunkMessage,
    WSMessageType.REGISTER_RESPONSE: RegisterResponseMessage,
    WSMessageType.CHUNK_RESPONSE: ChunkResponseMessage,
    WSMessageType.ERROR_RESPONSE: ErrorResponseMessage,
    WSMessageType.OK_RESPONSE: OkResponseMessage,
}


def decode_message(data: bytes) -> WSMessage:
    buf = BytesIO(data)

    msg_type = read_u8(buf)

    cls: type[WSMessage] | None = MESSAGE_TYPES.get(msg_type)
    if not cls:
        raise ValueError(f"Unknown message type {msg_type}")

    return cls.decode(buf)


def encode_message(msg: WSMessage) -> bytes:
    return msg.encode()
