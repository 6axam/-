"""Optional MTProto transport. Importing it does not connect to Telegram."""

from app.telegram.mtproto.client import MTProtoSettings, MTProtoTransport

__all__ = ("MTProtoSettings", "MTProtoTransport")
