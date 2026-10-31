from app.conversation.context import ContextBuilder
from app.conversation.lifecycle import ConversationLifecycleManager
from app.database.db import Database
from app.mind.world_state import SharedStateManager
from app.telegram.identity import ConversationIdentity, TelegramIdentityRegistry


async def test_context_history_is_strictly_scoped_to_chat_including_assistant_reactions_and_media(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'scope.sqlite'}")
    await db.connect()
    user_id, private_chat, group_chat = 7, 100, -200
    await db.ensure_user(user_id, "maksim")

    await db.record_message(
        chat_id=private_chat, telegram_message_id=1, user_id=user_id,
        sender="user", kind="text", text="PRIVATE_SECRET",
    )
    await db.record_message(
        chat_id=private_chat, telegram_message_id=2,
        sender="assistant", kind="text", text="PRIVATE_ASSISTANT_REPLY",
    )
    await db.record_reaction(
        chat_id=private_chat, telegram_message_id=2, actor="user",
        actor_user_id=user_id, emoji="❤️",
    )
    await db.record_message(
        chat_id=private_chat, telegram_message_id=3, user_id=user_id,
        sender="user", kind="photo", text="[photo]",
    )
    private_media = await db.record_media(
        chat_id=private_chat, telegram_message_id=3, file_id="private-photo",
        file_unique_id="private-photo-u", media_type="photo", mime_type="image/jpeg",
        local_cache_path=None, byte_size=None,
    )
    await db.execute(
        "INSERT INTO media_descriptions(media_id,description,model) VALUES(?,?,?)",
        (private_media, "PRIVATE_MEDIA_DESCRIPTION", "fake"),
    )

    await db.record_message(
        chat_id=group_chat, telegram_message_id=1, user_id=user_id,
        sender="user", kind="text", text="GROUP_MESSAGE",
    )
    await db.record_message(
        chat_id=group_chat, telegram_message_id=2,
        sender="assistant", kind="text", text="GROUP_ASSISTANT_REPLY",
    )
    await db.record_message(
        chat_id=group_chat, telegram_message_id=3, user_id=user_id,
        sender="user", kind="photo", text="[photo]",
    )
    group_media = await db.record_media(
        chat_id=group_chat, telegram_message_id=3, file_id="group-photo",
        file_unique_id="group-photo-u", media_type="photo", mime_type="image/jpeg",
        local_cache_path=None, byte_size=None,
    )
    await db.execute(
        "INSERT INTO media_descriptions(media_id,description,model) VALUES(?,?,?)",
        (group_media, "GROUP_MEDIA_DESCRIPTION", "fake"),
    )

    context = ContextBuilder(db)
    _, private_context = await context.build(user_id, private_chat, "привет")
    _, group_context = await context.build(user_id, group_chat, "привет")

    assert "PRIVATE_SECRET" in private_context
    assert "PRIVATE_ASSISTANT_REPLY" in private_context
    assert "PRIVATE_MEDIA_DESCRIPTION" in private_context
    assert "Собеседник поставил ❤️" in private_context
    assert "GROUP_MESSAGE" not in private_context
    assert "GROUP_ASSISTANT_REPLY" not in private_context
    assert "GROUP_MEDIA_DESCRIPTION" not in private_context

    assert "GROUP_MESSAGE" in group_context
    assert "GROUP_ASSISTANT_REPLY" in group_context
    assert "GROUP_MEDIA_DESCRIPTION" in group_context
    assert "PRIVATE_SECRET" not in group_context
    assert "PRIVATE_ASSISTANT_REPLY" not in group_context
    assert "PRIVATE_MEDIA_DESCRIPTION" not in group_context
    assert "Собеседник поставил ❤️" not in group_context
    await db.close()


async def test_mtproto_active_contact_roster_is_private_and_owner_labeled(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'roster.sqlite'}")
    await db.connect()
    shared = SharedStateManager(db)
    await shared.bootstrap()
    registry = TelegramIdentityRegistry(db)
    lifecycle = ConversationLifecycleManager(db, cooling_minutes=10, ended_hours=24)

    for user_id, chat_id, username in ((1, -1, "maksim_test"), (2, -2, "toma_test")):
        await db.ensure_user(user_id, username)
        await shared.ensure_contact(user_id, chat_id, username=username)
        await registry.register_conversation(
            ConversationIdentity("mtproto", 77, "user", user_id),
            local_chat_id=chat_id, username=username,
        )
        await registry.register_contact(
            transport="mtproto", account_id=77, telegram_user_id=user_id,
            local_contact_user_id=user_id, username=username,
        )
        await lifecycle.on_user_message(chat_id)

    await registry.register_conversation(
        ConversationIdentity("mtproto", 77, "supergroup", 99), local_chat_id=-99,
    )
    await lifecycle.on_user_message(-99)

    builder = ContextBuilder(
        db, owner_profile_user_id=1, transport_name="mtproto",
        active_contact_window_seconds=600,
    )
    _system, owner_context, owner_breakdown = await builder.build_with_breakdown(1, -1, "привет")
    _system, contact_context, contact_breakdown = await builder.build_with_breakdown(2, -2, "привет")

    assert "other_active_contacts=1" in owner_context
    assert "owner_visible_account_labels=@toma_test" in owner_context
    assert "other_active_contacts=1" in contact_context
    assert "@maksim_test" not in contact_context
    assert "supergroup" not in owner_context + contact_context
    assert owner_breakdown["components"]["active_private_contacts"]["tokens"] > 0
    assert contact_breakdown["components"]["active_private_contacts"]["tokens"] > 0
    await db.close()
