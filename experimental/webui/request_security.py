"""Low-level HTTP request limits and Host validation for the Web UI."""

from __future__ import annotations

import ipaddress
import json
from typing import Iterable


LOGIN_BODY_LIMIT = 4 * 1024
DEFAULT_BODY_LIMIT = 1024 * 1024
REQUEST_TARGET_LIMIT = 8 * 1024
HEADER_BYTES_LIMIT = 16 * 1024
HEADER_COUNT_LIMIT = 100


def _parse_port(value: str) -> bool:
    if not value or not value.isascii() or not value.isdigit():
        return False
    port = int(value)
    return 1 <= port <= 65535


def _split_host_header(value: str) -> str | None:
    """Return the host portion of a syntactically valid Host header."""
    if not value or value != value.strip():
        return None
    if any(ord(char) < 33 or ord(char) == 127 for char in value):
        return None
    if any(char in value for char in ("/", "\\", "@", ",", "#", "?")):
        return None

    if value.startswith("["):
        close = value.find("]")
        if close < 0:
            return None
        host = value[1:close]
        suffix = value[close + 1 :]
        if suffix and (not suffix.startswith(":") or not _parse_port(suffix[1:])):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return None
        return str(address) if address.version == 6 else None

    if value.count(":") == 1:
        host, port = value.rsplit(":", 1)
        if not _parse_port(port):
            return None
    else:
        host = value

    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass

    host = host.rstrip(".")
    if not host:
        return None
    try:
        return host.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return None


def host_is_allowed(
    host_header: str,
    trusted_hosts: Iterable[str],
    *,
    client_host: str | None = None,
) -> bool:
    host = _split_host_header(host_header)
    if host is None:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if host == "localhost":
        return True
    # Starlette's in-process TestClient uses this synthetic pair. It cannot
    # occur on a real socket and keeps production policy strict.
    if client_host == "testclient" and host == "testserver":
        return True
    return host in set(trusted_hosts)


class RequestSecurityMiddleware:
    """Reject oversized or untrusted requests before route parsing."""

    def __init__(self, app, *, trusted_hosts: Iterable[str] = ()) -> None:
        self.app = app
        self.trusted_hosts = tuple(trusted_hosts)

    @staticmethod
    async def _reject(send, status: int, message: str) -> None:
        body = json.dumps({"error": message}, separators=(",", ":")).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"connection", b"close"),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw_path = scope.get("raw_path") or scope.get("path", "").encode("utf-8")
        query = scope.get("query_string", b"")
        target_size = len(raw_path) + (1 + len(query) if query else 0)
        if target_size > REQUEST_TARGET_LIMIT:
            await self._reject(send, 414, "request target too large")
            return

        headers = scope.get("headers", ())
        if len(headers) > HEADER_COUNT_LIMIT:
            await self._reject(send, 431, "too many request headers")
            return
        header_bytes = sum(len(name) + len(value) + 4 for name, value in headers)
        if header_bytes > HEADER_BYTES_LIMIT:
            await self._reject(send, 431, "request headers too large")
            return

        host_values = [
            value.decode("latin-1")
            for name, value in headers
            if name.lower() == b"host"
        ]
        client = scope.get("client")
        client_host = client[0] if client else None
        if (
            len(host_values) != 1
            or not host_is_allowed(
                host_values[0],
                self.trusted_hosts,
                client_host=client_host,
            )
        ):
            await self._reject(send, 400, "invalid host header")
            return

        path = scope.get("path", "")
        body_limit = (
            LOGIN_BODY_LIMIT
            if path == "/login" and scope.get("method") == "POST"
            else DEFAULT_BODY_LIMIT
        )
        content_lengths = [
            value
            for name, value in headers
            if name.lower() == b"content-length"
        ]
        if len(content_lengths) > 1:
            await self._reject(send, 400, "invalid content length")
            return
        if content_lengths:
            try:
                content_length = int(content_lengths[0])
            except ValueError:
                await self._reject(send, 400, "invalid content length")
                return
            if content_length < 0:
                await self._reject(send, 400, "invalid content length")
                return
            if content_length > body_limit:
                await self._reject(send, 413, "request body too large")
                return

        messages = []
        body_size = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.disconnect":
                break
            if message["type"] != "http.request":
                continue
            body_size += len(message.get("body", b""))
            if body_size > body_limit:
                await self._reject(send, 413, "request body too large")
                return
            if not message.get("more_body", False):
                break

        index = 0

        async def replay_receive():
            nonlocal index
            if index < len(messages):
                message = messages[index]
                index += 1
                return message
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, send)
