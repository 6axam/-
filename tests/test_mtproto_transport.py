from datetime import datetime, timezone

import pytest

from app.telegram.mtproto.client import MTProtoSettings, MTProtoTransport
from app.telegram.transport import InboundKind, NormalizedInboundEvent


class FakeClient:
    def __init__(self): self.connected = self.disconnected = False; self.read_calls = []
    async def connect(self): self.connected = True
    async def disconnect(self): self.disconnected = True
    async def is_user_authorized(self): return True
    async def get_me(self): return type("Me", (), {"id": 12345678, "first_name": "Anya", "last_name": None})()
    async def get_input_entity(self, peer_id): return ("peer", peer_id)
    async def send_read_acknowledge(self, peer, message):
        self.read_calls.append((peer, message))
        return True


def test_normalized_idempotency_is_scoped_to_transport_account_peer_and_message():
    first = NormalizedInboundEvent("mtproto", 1, 100, 10, 7, InboundKind.text, datetime.now(timezone.utc))
    other_peer = NormalizedInboundEvent("mtproto", 1, 101, 10, 7, InboundKind.text)
    other_account = NormalizedInboundEvent("mtproto", 2, 100, 10, 7, InboundKind.text)
    assert first.idempotency_key != other_peer.idempotency_key
    assert first.idempotency_key != other_account.idempotency_key


async def test_mtproto_status_uses_fake_client_and_redacts_identity(tmp_path):
    fake = FakeClient()
    settings = MTProtoSettings(_env_file=None, telegram_api_id=1, telegram_api_hash="secret", telegram_session_path=str(tmp_path / "private" / "anya.session"), telegram_mtproto_dry_run=True)
    transport = MTProtoTransport(settings, client_factory=lambda *_: fake)
    status = await transport.status()
    assert status == {"transport": "mtproto", "connected": True, "authorized": True, "dry_run": True, "account_id_suffix": "5678", "display_name": "Anya"}
    assert (tmp_path / "private").stat().st_mode & 0o777 == 0o700
    await transport.close()
    assert fake.disconnected


async def test_mtproto_read_receipt_uses_a_resolved_peer_and_requires_acknowledgement(tmp_path):
    fake = FakeClient()
    settings = MTProtoSettings(_env_file=None, telegram_api_id=1, telegram_api_hash="secret", telegram_session_path=str(tmp_path / "anya.session"))
    transport = MTProtoTransport(settings, client_factory=lambda *_: fake)

    await transport.mark_read(42, 9)

    assert fake.read_calls == [(("peer", 42), 9)]


def test_mtproto_credentials_are_required_without_exposing_secret():
    with pytest.raises(RuntimeError, match="TELEGRAM_API_ID and TELEGRAM_API_HASH"):
        MTProtoSettings(_env_file=None).require_credentials()
