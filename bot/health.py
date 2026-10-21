"""Minimal HTTP health endpoint for container orchestration."""

from __future__ import annotations

import asyncio
import contextlib


async def start_health_server(port: int) -> asyncio.AbstractServer:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(asyncio.IncompleteReadError, TimeoutError):
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n"
            b"Connection: close\r\n\r\nOK"
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    return await asyncio.start_server(handle, "0.0.0.0", port)
