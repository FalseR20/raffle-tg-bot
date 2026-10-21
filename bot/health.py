"""Minimal HTTP health endpoint for container orchestration."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable


async def start_health_server(
    port: int,
    readiness: Callable[[], Awaitable[bool]] | None = None,
) -> asyncio.AbstractServer:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(asyncio.IncompleteReadError, TimeoutError):
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        healthy = True
        if readiness is not None:
            try:
                healthy = await readiness()
            except Exception:
                healthy = False
        status = "200 OK" if healthy else "503 Service Unavailable"
        body = b"OK" if healthy else b"NOT READY"
        headers = (
            f"HTTP/1.1 {status}\r\n"
            f"Content-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        writer.write(headers + body)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    return await asyncio.start_server(handle, "0.0.0.0", port)
