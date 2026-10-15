from app.conversation.context import ContextBuilder
from app.database.db import Database


async def make_db(tmp_path):
    db = Database(f"sqlite:///{tmp_path/'actor.sqlite'}")
    await db.connect(); await db.ensure_user(1, "maks")
    return db


async def add(db, sender, ident, text):
    await db.record_message(chat_id=10, telegram_message_id=ident, user_id=1 if sender == "user" else None, sender=sender, kind="text", text=text)


async def test_user_home_claim_is_actor_scoped_while_anya_is_at_college(tmp_path):
    db = await make_db(tmp_path)
    await add(db, "assistant", 1, "напишу, как домой приеду")
    await add(db, "user", 2, "я уже дома")
    _, context, breakdown = await ContextBuilder(db).build_with_breakdown(
        1, 10, "я уже дома", life_state={"phase": "college", "availability": "available", "event": None},
    )
    assert "MAXIM: я уже дома" in context
    assert "ANYA: напишу, как домой приеду" in context
    assert "phase=college" in context
    assert "has not arrived home" in context
    assert "ACTOR / TEMPORAL RULE" in context
    await db.close()


async def test_actor_and_conditional_promise_contract_is_in_system_prompt(tmp_path):
    db = await make_db(tmp_path)
    system, context, _ = await ContextBuilder(db).build_with_breakdown(
        1, 10, "меня отпустили с пар", life_state={"phase": "college", "availability": "available", "event": None}, self_life_gate_open=True,
    )
    assert "MAXIM's first person is MAXIM" in context
    assert "conditional promise due" in context
    assert "continuation_gate never confirms" in context
    assert "phase=college" in context and "continuation_gate=open" in context
    await db.close()


async def test_user_sleep_or_arrival_never_changes_anya_presence_context(tmp_path):
    db = await make_db(tmp_path)
    for index, text in enumerate(("я ложусь спать", "я уже приехал", "меня отпустили с пар"), 1):
        await add(db, "user", index, text)
    _, context, _ = await ContextBuilder(db).build_with_breakdown(
        1, 10, "я уже приехал", life_state={"phase": "college", "availability": "available", "event": None},
    )
    assert context.count("phase=college") >= 1
    assert "MAXIM: я ложусь спать" in context
    await db.close()
