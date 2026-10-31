from app.conversation.context import ContextBuilder
from app.conversation.workload import ConversationWorkload
from app.database.db import Database
from app.llm.schemas import MemoryCandidate
from app.memory.manager import MemoryManager
from app.social.contact_sharing import ContactMemorySharing


async def _memory(db, *, user_id, chat_id, content):
    manager = MemoryManager(db)
    await manager.apply(user_id, chat_id, [MemoryCandidate(
        decision="SAVE", content=content, importance=.8, confidence=.9, tags=["project"],
    )])
    return (await db.fetchone("SELECT id FROM memories WHERE user_id=? AND chat_id=?", (user_id, chat_id)))["id"]


async def test_explicit_contact_fact_sharing_is_scoped_and_revocable(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'sharing.sqlite'}")
    await db.connect()
    await db.ensure_user(1, "maksim_test")
    await db.ensure_user(2, "seriy_test")
    await db.ensure_user(3, "third_test")
    linux = await _memory(db, user_id=1, chat_id=-1, content="Максим ведёт публичный Linux-проект")
    secret = await _memory(db, user_id=1, chat_id=-1, content="Максим тяжело переживает личный конфликт")
    sharing = ContactMemorySharing(db)

    assert await sharing.grant(memory_id=linux, owner_user_id=1, visibility="shareable", allowed_audience=[2])
    assert not await sharing.grant(memory_id=secret + 999, owner_user_id=1, visibility="public")
    assert [fact.content for fact in await sharing.relevant_for(audience_user_id=2, query="что за Linux проект")] == ["Максим ведёт публичный Linux-проект"]
    assert await sharing.relevant_for(audience_user_id=3, query="что за Linux проект") == []
    assert await sharing.relevant_for(audience_user_id=2, query="личный конфликт") == []

    builder = ContextBuilder(db, owner_profile_user_id=1, contact_memory_sharing=sharing)
    other_system, other_context, breakdown = await builder.build_with_breakdown(2, -2, "расскажи про Linux проект")
    assert "Максим ведёт публичный Linux-проект" in other_context
    assert "личный конфликт" not in other_context
    assert "Lenovo ThinkPad T14" not in other_system
    assert breakdown["components"]["shared_contact_facts"]["tokens"] > 0

    assert await sharing.revoke(memory_id=linux, owner_user_id=1)
    assert await sharing.relevant_for(audience_user_id=2, query="Linux проект") == []
    await db.close()


async def test_owner_profile_fails_closed_without_confirmed_owner_id(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'profile.sqlite'}")
    await db.connect()
    await db.ensure_user(2, "seriy_test")

    system, _context, breakdown = await ContextBuilder(db).build_with_breakdown(2, -2, "привет")

    assert "Lenovo ThinkPad T14" not in system
    assert breakdown["components"]["user_profile"]["tokens"] == 0
    await db.close()


async def test_workload_counts_other_pending_work_without_cross_chat_content(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'workload.sqlite'}")
    await db.connect()
    await db.record_message(chat_id=-2, telegram_message_id=1, user_id=2, sender="user", kind="text", text="queued")
    await db.execute(
        "INSERT INTO scheduled_responses(chat_id,user_id,respond_after,generation_id,status,transport) VALUES(?,?,datetime('now'),?,'pending','mtproto')",
        (-2, 2, "other"),
    )
    blocker = asyncio.Event()
    active = asyncio.create_task(blocker.wait())
    workload = ConversationWorkload(db, transport="mtproto")
    workload.bind_manager(SimpleNamespace(requests={-1: asyncio.create_task(asyncio.sleep(0)), -2: active}))
    snapshot = await workload.snapshot(-1)

    assert snapshot.active_generations == 1
    assert snapshot.pending_messages == 1
    assert snapshot.pending_responses == 1
    assert snapshot.pressure == "moderate"
    active.cancel()
    await asyncio.gather(active, return_exceptions=True)
    await db.close()
import asyncio
from types import SimpleNamespace
