import pytest

from app.database.db import Database
from app.mind.world_state import SharedStateManager
from app.telegram.identity import ConversationIdentity, TelegramIdentityRegistry


@pytest.fixture
async def db(tmp_path):
    value = Database(f"sqlite:///{tmp_path / 'identities.sqlite'}")
    await value.connect()
    yield value
    await value.close()


async def test_composite_identity_keeps_accounts_and_contexts_distinct_but_links_one_person(db):
    shared = SharedStateManager(db)
    await shared.bootstrap()
    await shared.ensure_contact(42, -42, username="old_name")
    registry = TelegramIdentityRegistry(db)

    private = ConversationIdentity("mtproto", 100, "user", 42)
    group = ConversationIdentity("mtproto", 100, "supergroup", 777, thread_id=13)
    other_account = ConversationIdentity("mtproto", 200, "user", 42)
    await registry.register_conversation(private, local_chat_id=-42, username="old_name")
    await registry.register_conversation(group, local_chat_id=-77713, title="allowed group")
    await registry.register_conversation(other_account, local_chat_id=-2042, username="same_numeric_id_elsewhere")
    await registry.register_contact(
        transport="mtproto", account_id=100, telegram_user_id=42,
        local_contact_user_id=42, username="new_name", display_name="New Name",
    )

    assert (await registry.conversation(private))["local_chat_id"] == -42
    assert (await registry.conversation(group))["local_chat_id"] == -77713
    assert (await registry.conversation(other_account))["local_chat_id"] == -2042
    assert await registry.resolve_contact(transport="mtproto", account_id=100, telegram_user_id=42) == 42
    assert await registry.resolve_contact(transport="mtproto", account_id=200, telegram_user_id=42) is None
    row = await db.fetchone(
        "SELECT username,display_name FROM telegram_contact_identities WHERE transport='mtproto' AND account_id=100 AND telegram_user_id=42",
    )
    assert tuple(row) == ("new_name", "New Name")
    # The same contact can be linked to group context without merging the
    # group's conversation history into their private chat.
    await shared.ensure_contact(42, -77713, context_kind="group")
    contexts = await db.fetchall(
        "SELECT chat_id,context_kind FROM anya_conversation_contacts WHERE telegram_user_id=42 ORDER BY chat_id",
    )
    assert [tuple(row) for row in contexts] == [(-77713, "group"), (-42, "private")]


def test_identity_rejects_ambiguous_or_invalid_peer_types():
    with pytest.raises(ValueError, match="peer type"):
        ConversationIdentity("mtproto", 1, "unknown", 1)
    with pytest.raises(ValueError, match="account_id"):
        ConversationIdentity("mtproto", 0, "user", 1)
