import asyncio

import pytest

from app.actions.models import Action, ActionType
from app.actions.queue import ActionQueue
from app.conversation.context import ContextBuilder
from app.conversation.manager import ConversationManager
from app.database.db import Database
from app.emotions.engine import EmotionalEngine
from app.llm.schemas import EmotionDelta, LLMResponse


class Executor:
    async def execute(self, _item):
        return None


class Provider:
    async def generate(self, _request):
        return LLMResponse(actions=[Action(type=ActionType.text, text="ok")], emotion_delta=EmotionDelta(warmth=.05))


@pytest.mark.asyncio
async def test_accepted_response_applies_delta_and_context_is_compact(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'state.sqlite'}")
    await db.connect()
    engine = EmotionalEngine(db)
    context = ContextBuilder(db, emotion_engine=engine)
    manager = ConversationManager(Provider(), context, ActionQueue(Executor()), emotion_engine=engine)
    await manager.handle_turn(1, 9, "привет")
    await asyncio.sleep(0)
    assert (await engine.get(9)).warmth == pytest.approx(.70)
    _, rendered = await context.build(1, 9, "как ты")
    assert "EMOTIONAL CONTINUITY" in rendered and "warmth=0.70" in rendered
    await db.close()


@pytest.mark.asyncio
async def test_failed_provider_does_not_apply_model_delta(tmp_path):
    class FailingProvider:
        async def generate(self, _request):
            raise RuntimeError("offline")
    db = Database(f"sqlite:///{tmp_path / 'failed.sqlite'}")
    await db.connect()
    engine = EmotionalEngine(db)
    manager = ConversationManager(FailingProvider(), ContextBuilder(db, emotion_engine=engine), ActionQueue(Executor()), emotion_engine=engine)
    await manager.handle_turn(1, 9, "привет")
    assert (await engine.get(9)).warmth == pytest.approx(.65)
    await db.close()
