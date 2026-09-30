from app.conversation.context import ContextBuilder
from app.database.db import Database


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
    assert "Максим поставил ❤️" in private_context
    assert "GROUP_MESSAGE" not in private_context
    assert "GROUP_ASSISTANT_REPLY" not in private_context
    assert "GROUP_MEDIA_DESCRIPTION" not in private_context

    assert "GROUP_MESSAGE" in group_context
    assert "GROUP_ASSISTANT_REPLY" in group_context
    assert "GROUP_MEDIA_DESCRIPTION" in group_context
    assert "PRIVATE_SECRET" not in group_context
    assert "PRIVATE_ASSISTANT_REPLY" not in group_context
    assert "PRIVATE_MEDIA_DESCRIPTION" not in group_context
    assert "Максим поставил ❤️" not in group_context
    await db.close()
