from datetime import datetime, timedelta, timezone

import pytest

from app.database.db import Database
from app.emotions.engine import EmotionalEngine
from app.emotions.model import EMOTION_BASELINE, EmotionDelta


@pytest.fixture
async def engine(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'emotions.sqlite'}")
    await db.connect()
    yield EmotionalEngine(db)
    await db.close()


@pytest.mark.asyncio
async def test_default_state_is_canonical_and_per_chat(engine):
    one, two = await engine.get(10), await engine.get(20)
    assert one.values() == EMOTION_BASELINE
    assert two.values() == EMOTION_BASELINE


@pytest.mark.asyncio
async def test_sparse_delta_is_bounded_and_persistent(engine):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state = await engine.apply_delta(10, {"warmth": .9, "hurt": -.9, "unknown": .9}, now=now)
    assert state.warmth == pytest.approx(.77)
    assert state.hurt == pytest.approx(0)
    assert (await engine.get(10, now=now)).warmth == pytest.approx(.77)
    assert (await engine.get(20, now=now)).warmth == pytest.approx(.65)


@pytest.mark.asyncio
async def test_elapsed_time_moves_state_toward_baseline_and_raises_absence_need(engine):
    start = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    await engine.apply_delta(10, EmotionDelta({"irritation": .12, "social_need": -.12}), now=start)
    later = await engine.advance(10, now=start + timedelta(hours=10))
    assert .05 < later.irritation < .17
    assert later.social_need > .33


@pytest.mark.asyncio
async def test_advance_is_idempotent_for_same_clock_and_uses_local_night(engine):
    at_night = datetime(2026, 1, 1, 23, tzinfo=timezone.utc)
    await engine.get(10, now=at_night)
    first = await engine.advance(10, now=at_night + timedelta(hours=4), sleeping=True)
    second = await engine.advance(10, now=at_night + timedelta(hours=4), sleeping=True)
    assert first == second
    assert first.fatigue > .25
