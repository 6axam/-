import asyncio

from app.database.db import Database
from app.events import EventEnvelope, EventJournal, EventProcessor


async def test_event_journal_is_append_only_and_idempotent(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'events.sqlite'}")
    await db.connect()
    journal = EventJournal(db)
    first = EventEnvelope(
        event_type="incoming_message", source="test", actor_kind="contact", actor_id="10",
        scope="conversation", idempotency_key="telegram:100:1", payload={"chat_id": 100},
    )
    row, created = await journal.append(first)
    duplicate, duplicate_created = await journal.append(EventEnvelope(
        event_type="incoming_message", source="test", actor_kind="contact", actor_id="10",
        scope="conversation", idempotency_key="telegram:100:1", payload={"chat_id": 100},
    ))

    assert created and not duplicate_created
    assert duplicate["event_id"] == row["event_id"]
    assert (await db.fetchone("SELECT COUNT(*) AS count FROM anya_events"))["count"] == 1
    assert await journal.transition(row["event_id"], "affective_transition", before={"stress": .1}, after={"stress": .2})
    assert not await journal.transition(row["event_id"], "affective_transition", before={"stress": .2}, after={"stress": .3})
    event = await db.fetchone("SELECT status FROM anya_events WHERE event_id=?", (row["event_id"],))
    assert event["status"] == "applied"
    await db.close()


async def test_event_processor_serializes_duplicate_ingestion(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'events.sqlite'}")
    await db.connect()
    processor = EventProcessor(EventJournal(db))

    def envelope():
        return EventEnvelope(event_type="time_tick", source="test", actor_kind="system", scope="global", idempotency_key="tick:1")

    results = await asyncio.gather(*(processor.ingest(envelope()) for _ in range(4)))
    assert sum(created for _, created, _ in results) == 1
    assert (await db.fetchone("SELECT COUNT(*) AS count FROM anya_events"))["count"] == 1
    await db.close()
