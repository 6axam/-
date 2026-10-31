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


async def test_assistant_message_is_linked_to_generation_turn(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'bot.sqlite'}")
    await db.connect()
    await db.ensure_user(10, "owner")
    await db.record_message(chat_id=100, telegram_message_id=1, user_id=10,
                            sender="user", kind="text", text="hello")
    turn_id = await db.ensure_turn_for_batch(
        user_id=10, chat_id=100, merged_text="hello", telegram_message_ids=[1],
    )
    await db.execute(
        "INSERT INTO llm_generations(chat_id,turn_id,generation_id,kind,model,latency_ms) "
        "VALUES(?,?,?,?,?,?)", (100, turn_id, "generation-1", "conversation", "test", 120),
    )
    assistant_id = await db.record_message(
        chat_id=100, telegram_message_id=2, sender="assistant", kind="text",
        text="hi", generation_id="generation-1",
    )
    row = await db.fetchone("SELECT conversation_turn_id FROM messages WHERE id=?", (assistant_id,))
    assert row["conversation_turn_id"] == turn_id
    await db.close()
