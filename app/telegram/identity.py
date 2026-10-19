"""Stable, transport-scoped Telegram identities.

Legacy runtime components still use an integer ``chat_id``.  This registry
keeps that value as a backwards-compatible context handle while storing the
actual Telegram identity separately, so a user can be the same contact in a
private chat and a group without making those conversations the same context.
"""
from __future__ import annotations

from dataclasses import dataclass


PEER_TYPES = frozenset({"user", "group", "supergroup", "channel"})


@dataclass(frozen=True)
class ConversationIdentity:
    transport: str
    account_id: int
    peer_type: str
    peer_id: int
    thread_id: int = 0

    def __post_init__(self):
        if not self.transport or self.account_id <= 0 or self.peer_id == 0:
            raise ValueError("transport, positive account_id and non-zero peer_id are required")
        if self.peer_type not in PEER_TYPES:
            raise ValueError(f"unsupported Telegram peer type: {self.peer_type}")
        if self.thread_id < 0:
            raise ValueError("thread_id cannot be negative")


class TelegramIdentityRegistry:
    """Idempotently map account/peer/topic identity to existing local state."""

    def __init__(self, db):
        self.db = db

    async def register_conversation(
        self, identity: ConversationIdentity, *, local_chat_id: int,
        title: str | None = None, username: str | None = None,
    ) -> ConversationIdentity:
        await self.db.execute(
            "INSERT INTO telegram_accounts(transport,account_id) VALUES(?,?) ON CONFLICT DO NOTHING",
            (identity.transport, identity.account_id),
        )
        await self.db.execute(
            "INSERT INTO telegram_peers(transport,account_id,peer_type,peer_id,title,username) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(transport,account_id,peer_type,peer_id) DO UPDATE SET "
            "title=COALESCE(excluded.title,telegram_peers.title),username=COALESCE(excluded.username,telegram_peers.username),updated_at=CURRENT_TIMESTAMP",
            (identity.transport, identity.account_id, identity.peer_type, identity.peer_id, title, username),
        )
        await self.db.execute(
            "INSERT INTO telegram_conversations(transport,account_id,peer_type,peer_id,thread_id,local_chat_id) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(transport,account_id,peer_type,peer_id,thread_id) DO UPDATE SET updated_at=CURRENT_TIMESTAMP",
            (identity.transport, identity.account_id, identity.peer_type, identity.peer_id, identity.thread_id, local_chat_id),
        )
        return identity

    async def register_contact(
        self, *, transport: str, account_id: int, telegram_user_id: int,
        local_contact_user_id: int, username: str | None = None,
        display_name: str | None = None,
    ) -> None:
        """Link a protocol user identity to the existing contact profile.

        Username is mutable display metadata and is never used for identity.
        """
        await self.db.execute(
            "INSERT INTO telegram_accounts(transport,account_id) VALUES(?,?) ON CONFLICT DO NOTHING",
            (transport, account_id),
        )
        await self.db.execute(
            "INSERT INTO telegram_contact_identities(transport,account_id,telegram_user_id,local_contact_user_id,username,display_name) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(transport,account_id,telegram_user_id) DO UPDATE SET "
            "local_contact_user_id=excluded.local_contact_user_id,username=COALESCE(excluded.username,telegram_contact_identities.username),"
            "display_name=COALESCE(excluded.display_name,telegram_contact_identities.display_name),last_seen_at=CURRENT_TIMESTAMP",
            (transport, account_id, telegram_user_id, local_contact_user_id, username, display_name),
        )

    async def resolve_contact(self, *, transport: str, account_id: int, telegram_user_id: int) -> int | None:
        row = await self.db.fetchone(
            "SELECT local_contact_user_id FROM telegram_contact_identities WHERE transport=? AND account_id=? AND telegram_user_id=?",
            (transport, account_id, telegram_user_id),
        )
        return int(row["local_contact_user_id"]) if row else None

    async def conversation(self, identity: ConversationIdentity):
        return await self.db.fetchone(
            "SELECT * FROM telegram_conversations WHERE transport=? AND account_id=? AND peer_type=? AND peer_id=? AND thread_id=?",
            (identity.transport, identity.account_id, identity.peer_type, identity.peer_id, identity.thread_id),
        )
