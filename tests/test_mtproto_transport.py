from datetime import datetime, timezone

import pytest

from app.telegram.mtproto.client import MTProtoSettings, MTProtoTransport
from app.telegram.transport import InboundKind, NormalizedInboundEvent


class FakeClient:
    def __init__(self, *, rpc_result=True, watermark=0):
        self.connected = self.disconnected = False; self.read_calls = []
        self.rpc_result, self.watermark = rpc_result, watermark
    async def connect(self): self.connected = True
    async def disconnect(self): self.disconnected = True
    async def is_user_authorized(self): return True
    async def get_me(self): return type("Me", (), {"id": 12345678, "first_name": "Anya", "last_name": None})()
    async def get_input_entity(self, peer_id): return ("peer", peer_id)
    async def send_read_acknowledge(self, peer, *, max_id):
        self.read_calls.append((peer, max_id))
        return self.rpc_result
    async def __call__(self, _request):
        return type("Dialogs", (), {"dialogs": [type("Dialog", (), {"read_inbox_max_id": self.watermark})()]})()


def test_normalized_idempotency_is_scoped_to_transport_account_peer_and_message():
    first = NormalizedInboundEvent("mtproto", 1, 100, 10, 7, InboundKind.text, datetime.now(timezone.utc))
    other_peer = NormalizedInboundEvent("mtproto", 1, 101, 10, 7, InboundKind.text)
    other_account = NormalizedInboundEvent("mtproto", 2, 100, 10, 7, InboundKind.text)
    assert first.idempotency_key != other_peer.idempotency_key
    assert first.idempotency_key != other_account.idempotency_key


def test_mtproto_capabilities_describe_only_the_live_adapter_actions():
    capabilities = MTProtoTransport.capabilities
    assert capabilities.reactions and capabilities.read_receipts and capabilities.stickers and capabilities.voice and capabilities.typing
    assert not capabilities.edit and not capabilities.delete and not capabilities.albums and not capabilities.contact_block


async def test_mtproto_status_uses_fake_client_and_redacts_identity(tmp_path):
    fake = FakeClient()
    settings = MTProtoSettings(_env_file=None, telegram_api_id=1, telegram_api_hash="secret", telegram_session_path=str(tmp_path / "private" / "anya.session"), telegram_mtproto_dry_run=True)
    transport = MTProtoTransport(settings, client_factory=lambda *_: fake)
    status = await transport.status()
    assert status == {"transport": "mtproto", "connected": True, "authorized": True, "dry_run": True, "account_id_suffix": "5678", "display_name": "Anya"}
    assert (tmp_path / "private").stat().st_mode & 0o777 == 0o700
    await transport.close()
    assert fake.disconnected


async def test_mtproto_read_receipt_uses_a_resolved_peer_and_confirms_server_watermark(tmp_path):
    # Telethon can return False even when the request was a harmless no-op;
    # only the independently queried server watermark confirms the receipt.
    fake = FakeClient(rpc_result=False, watermark=9)
    settings = MTProtoSettings(_env_file=None, telegram_api_id=1, telegram_api_hash="secret", telegram_session_path=str(tmp_path / "anya.session"))
    transport = MTProtoTransport(settings, client_factory=lambda *_: fake)

    acknowledgement = await transport.mark_read(42, 9)

    assert fake.read_calls == [(("peer", 42), 9)]
    assert acknowledgement.requested is True
    assert acknowledgement.rpc_returned_true is False
    assert acknowledgement.server_watermark == 9
    assert acknowledgement.confirmed is True


async def test_mtproto_read_receipt_waits_when_server_watermark_is_behind(tmp_path):
    fake = FakeClient(rpc_result=True, watermark=8)
    settings = MTProtoSettings(_env_file=None, telegram_api_id=1, telegram_api_hash="secret", telegram_session_path=str(tmp_path / "anya.session"))
    acknowledgement = await MTProtoTransport(settings, client_factory=lambda *_: fake).mark_read(42, 9)

    assert acknowledgement.requested is True
    assert acknowledgement.confirmed is False
    assert acknowledgement.server_watermark == 8


def test_mtproto_credentials_are_required_without_exposing_secret():
    with pytest.raises(RuntimeError, match="TELEGRAM_API_ID and TELEGRAM_API_HASH"):
        MTProtoSettings(_env_file=None).require_credentials()
