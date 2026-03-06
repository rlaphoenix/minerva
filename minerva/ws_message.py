from typing import Any


class PayloadStruct:
    def __init__(self, buffer: bytes | None = None):
        self._buffer = buffer or bytes()
        self._pos = 0

    def add_string(self, string: str) -> None:
        self._buffer += len(string).to_bytes(4, "little", signed=False)
        self._buffer += string.encode("utf8")
        self._pos = len(self._buffer) - 1

    def get_string(self) -> str:
        data = self._buffer[self._pos : self._pos + 4]
        length = int.from_bytes(data, "little", signed=False)
        self._pos += 4 + length
        return self._buffer[self._pos - length : self._pos].decode("utf8")

    def add_bytes(self, data: bytes) -> None:
        self._buffer += len(data).to_bytes(4, "little", signed=False)
        self._buffer += data
        self._pos = len(self._buffer) - 1

    def get_bytes(self) -> bytes:
        data = self._buffer[self._pos : self._pos + 4]
        length = int.from_bytes(data, "little", signed=False)
        self._pos += 4 + length
        return self._buffer[self._pos - length : self._pos]

    def add_integer(self, integer: int) -> None:
        self._buffer += integer.to_bytes(4, "little", signed=False)

    def get_integer(self) -> int:
        self._pos += 4
        return int.from_bytes(self._buffer[self._pos - 4 : self._pos], "little", signed=False)

    def get_buffer(self) -> bytes:
        return self._buffer

    def add_byte(self, integer: int) -> None:
        self._buffer += integer.to_bytes(1, "little", signed=False)

    def get_byte(self) -> int:
        self._pos += 1
        return self._buffer[self._pos - 1]


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
        self._type = type
        self._payload = payload

    def get_type(self) -> int:
        return self._type

    def get_payload(self) -> dict[str, Any]:
        return self._payload

    def encode(self) -> bytes:
        encoded = PayloadStruct()
        encoded.add_byte(self._type)
        if self._type == WSMessageType.REGISTER:
            encoded.add_integer(self._payload["version"])
            encoded.add_integer(self._payload["max_concurrent"])
            encoded.add_string(self._payload["access_token"])
        if self._type == WSMessageType.UPLOAD_SUBCHUNK:
            encoded.add_string(self._payload["chunk_id"])
            encoded.add_string(self._payload["file_id"])
            encoded.add_bytes(self._payload["payload"])
        if self._type == WSMessageType.GET_CHUNKS:
            encoded.add_integer(self._payload["count"])
        if self._type == WSMessageType.DETACH_CHUNK:
            encoded.add_string(self._payload["chunk_id"])
        if self._type == WSMessageType.REGISTER_RESPONSE:
            encoded.add_string(self._payload["worker_id"])
        if self._type == WSMessageType.CHUNK_RESPONSE:
            encoded.add_integer(len(self._payload))
            for chunk_id in self._payload:
                encoded.add_string(chunk_id)
                encoded.add_string(self._payload[chunk_id]["file_id"])
                encoded.add_string(self._payload[chunk_id]["url"])
                encoded.add_integer(self._payload[chunk_id]["range"][0])
                encoded.add_integer(self._payload[chunk_id]["range"][1])
        if self._type == WSMessageType.ERROR_RESPONSE or self._type == WSMessageType.OK_RESPONSE:
            encoded.add_integer(len(self._payload))
            for key in self._payload:
                encoded.add_string(key)
                encoded.add_string(self._payload[key])
        return encoded.get_buffer()

    @staticmethod
    def decode(encoded: bytes) -> "WSMessage":
        struct = PayloadStruct(encoded)
        type = struct.get_byte()
        payload: dict[str, Any] = {}
        if type == WSMessageType.REGISTER:
            payload["version"] = struct.get_integer()
            payload["max_concurrent"] = struct.get_integer()
            payload["access_token"] = struct.get_string()
        if type == WSMessageType.UPLOAD_SUBCHUNK:
            payload["chunk_id"] = struct.get_string()
            payload["file_id"] = struct.get_string()
            payload["payload"] = struct.get_bytes()
        if type == WSMessageType.GET_CHUNKS:
            payload["count"] = struct.get_integer()
        if type == WSMessageType.DETACH_CHUNK:
            payload["chunk_id"] = struct.get_string()
        if type == WSMessageType.REGISTER_RESPONSE:
            payload["worker_id"] = struct.get_string()
        if type == WSMessageType.CHUNK_RESPONSE:
            payload_length = struct.get_integer()
            for i in range(payload_length):
                chunk_id = struct.get_string()
                file_id = struct.get_string()
                url = struct.get_string()
                start = struct.get_integer()
                end = struct.get_integer()
                payload[chunk_id] = {"file_id": file_id, "url": url, "range": [start, end]}
        if type == WSMessageType.ERROR_RESPONSE or type == WSMessageType.OK_RESPONSE:
            payload_length = struct.get_integer()
            for _ in range(payload_length):
                key = struct.get_string()
                value = struct.get_string()
                payload[key] = value
        return WSMessage(type, payload)
