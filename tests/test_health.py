import asyncio
import socket

import pytest

from app.health import serve_database_health


class _Database:
    async def fetchone(self, query):
        assert query == "SELECT 1"
        return {"1": 1}


@pytest.mark.asyncio
async def test_database_health_endpoint_returns_ok():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    task = asyncio.create_task(serve_database_health(_Database(), port=port))
    try:
        for _ in range(50):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                break
            except OSError:
                await asyncio.sleep(0.01)
        else:
            pytest.fail("health endpoint did not start")
        writer.write(b"GET /healthz HTTP/1.1\r\nHost: localhost\r\n\r\n")
        await writer.drain()
        response = await reader.read()
        writer.close()
        await writer.wait_closed()
        assert response.startswith(b"HTTP/1.1 200 OK\r\n")
        assert response.endswith(b"ok\n")
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_database_health_endpoint_hides_other_paths():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    task = asyncio.create_task(serve_database_health(_Database(), port=port))
    try:
        for _ in range(50):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                break
            except OSError:
                await asyncio.sleep(0.01)
        else:
            pytest.fail("health endpoint did not start")
        writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        await writer.drain()
        response = await reader.read()
        writer.close()
        await writer.wait_closed()
        assert response.startswith(b"HTTP/1.1 404 Not Found\r\n")
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
