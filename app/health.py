"""Private liveness endpoint for container health checks."""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)


async def serve_database_health(db, host: str = "127.0.0.1", port: int = 8765) -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        status = "200 OK"
        body = b"ok\n"
        try:
            request = await asyncio.wait_for(reader.readline(), timeout=2)
            while await asyncio.wait_for(reader.readline(), timeout=2) not in {b"\r\n", b"\n", b""}:
                pass
            if not request.startswith(b"GET /healthz "):
                status, body = "404 Not Found", b"not found\n"
            elif not await db.fetchone("SELECT 1"):
                status, body = "503 Service Unavailable", b"database unavailable\n"
        except Exception:
            log.warning("health_check_failed", exc_info=True)
            status, body = "503 Service Unavailable", b"unavailable\n"
        writer.write(
            f"HTTP/1.1 {status}\r\nContent-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii") + body
        )
        try:
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, host, port)
    async with server:
        await server.serve_forever()
