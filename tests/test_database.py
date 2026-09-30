from app.database.db import Database


async def test_database_smoke_insert_read_close(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'bot.sqlite'}")
    await db.connect()
    await db.ensure_user(10, "owner")
    message_id = await db.record_message(chat_id=100, telegram_message_id=1, user_id=10, sender="user", kind="text", text="hello")
    assert message_id is not None
    row = await db.fetchone("SELECT chat_id, telegram_message_id, text FROM messages WHERE id=?", (message_id,))
    assert tuple(row) == (100, 1, "hello")
    await db.close()


async def test_message_idempotency_is_scoped_to_chat(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'bot.sqlite'}")
    await db.connect()
    first = await db.record_message(chat_id=100, telegram_message_id=5, user_id=10, sender="user", kind="text", text="one")
    duplicate = await db.record_message(chat_id=100, telegram_message_id=5, user_id=10, sender="user", kind="text", text="one")
    other_chat = await db.record_message(chat_id=101, telegram_message_id=5, user_id=10, sender="user", kind="text", text="two")
    assert first is not None and duplicate is None and other_chat is not None
    rows = await db.fetchall("SELECT id FROM messages")
    assert len(rows) == 2
    await db.close()


async def test_telegram_update_idempotency(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'bot.sqlite'}")
    await db.connect()
    assert await db.claim_update(999)
    assert not await db.claim_update(999)
    await db.close()
