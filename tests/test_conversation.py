import asyncio

from app.actions.models import Action, ActionType
from app.actions.queue import ActionQueue
from app.conversation.manager import ConversationManager
from app.llm.schemas import LLMResponse


class FakeContext:
    async def build(self, _user_id, _chat_id, _text):
        return "system", "context"


class FakeExecutor:
    def __init__(self): self.sent = []
    async def execute(self, item): self.sent.append(item.action.text)


class CancellationSwallowingProvider:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
    async def generate(self, _request):
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            pass
        return LLMResponse(actions=[Action(type=ActionType.text, text="stale")])


async def test_stale_response_cannot_enqueue_actions_after_interrupt():
    provider = CancellationSwallowingProvider()
    executor = FakeExecutor()
    manager = ConversationManager(provider, FakeContext(), ActionQueue(executor))
    work = asyncio.create_task(manager.handle_turn(1, 100, "old turn"))
    await provider.started.wait()
    manager.interrupt(100)
    provider.release.set()
    await work
    await asyncio.sleep(.02)
    assert executor.sent == []
