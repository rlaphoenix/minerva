import socket
import time
from typing import Any


def cache_dns() -> None:
    dns_cache: dict[
        tuple[str, int],
        list[
            tuple[
                socket.AddressFamily,
                socket.SocketKind,
                int,
                str,
                tuple[str, int] | tuple[str, int, int, int] | tuple[int, bytes],
            ]
        ],
    ] = {}
    dns_cache_times: dict[tuple[str, int], float] = {}
    stale_time_seconds = 60.0

    _orig_getaddrinfo = socket.getaddrinfo

    def cached_getaddrinfo(host: str, port: int, *args: Any, **kwargs: Any) -> list:
        key = (host, port)

        now = time.monotonic()
        if (key not in dns_cache) or now - dns_cache_times[key] > stale_time_seconds:
            dns_cache[key] = _orig_getaddrinfo(host, port, *args, **kwargs)
            dns_cache_times[key] = now

        return dns_cache[key]

    socket.getaddrinfo = cached_getaddrinfo  # type: ignore


cache_dns()
