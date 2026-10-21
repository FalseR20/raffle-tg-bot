import asyncio

from bot.health import start_health_server


async def test_health_server_returns_ok():
    server = await start_health_server(0)
    port = server.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
        await writer.drain()
        response = await reader.read(200)
        writer.close()
        await writer.wait_closed()
        assert response.startswith(b"HTTP/1.1 200 OK")
        assert response.endswith(b"OK")
    finally:
        server.close()
        await server.wait_closed()
