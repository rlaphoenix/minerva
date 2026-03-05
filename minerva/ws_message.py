import pickle
from typing import Any


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
    def __init__(self, type: int, payload: Any):
        self._type = type
        self._payload = payload

    def get_type(self) -> int:
        return self._type

    def get_payload(self) -> Any:
        return self._payload

    def encode(self) -> bytes:
        encoded = self._type.to_bytes(1, "little", signed=False)
        pickled = pickle.dumps(self._payload, protocol=pickle.HIGHEST_PROTOCOL)
        encoded += pickled
        return encoded

    @staticmethod
    def decode(encoded: bytes) -> "WSMessage":
        return WSMessage(encoded[0], pickle.loads(encoded[1:]))
