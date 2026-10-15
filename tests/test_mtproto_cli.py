import asyncio

from app.telegram.mtproto.client import MTProtoSettings, MTProtoTransport


class CancelledDisconnectClient:
    async def disconnect(self):
        raise asyncio.CancelledError


async def test_mtproto_close_tolerates_interactive_cancellation(tmp_path):
    transport = MTProtoTransport(MTProtoSettings(telegram_api_id=1, telegram_api_hash="a" * 32, telegram_session_path=str(tmp_path / "anya.session")))
    transport.client = CancelledDisconnectClient()
    await transport.close()
