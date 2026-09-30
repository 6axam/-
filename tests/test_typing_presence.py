from types import SimpleNamespace

from app.actions.models import Action, ActionType
from app.actions.queue import ActionQueue
from app.conversation.manager import ConversationManager
from app.llm.schemas import LLMResponse, ResponseTiming


class PresenceExecutor:
    def __init__(self):
        self.events = []

    async def start_generation_typing(self, _chat_id):
        self.events.append("start")
        return object()

    async def stop_generation_typing(self, _session):
        self.events.append("stop")

    async def execute(self, _item):
        pass


class Context:
    async def build(self, *_args):
        return "system", "context"


async def test_typing_starts_before_primary_llm_request_and_stops_after():
    executor = PresenceExecutor()

    class Provider:
        async def generate(self, _request):
            assert executor.events == ["start"]
            return LLMResponse(actions=[])

    manager = ConversationManager(Provider(), Context(), ActionQueue(executor))
    await manager.handle_turn(1, 10, "привет")
    assert executor.events == ["start", "stop"]


async def test_typing_covers_timing_request_before_primary_generation():
    executor = PresenceExecutor()

    class Scheduler:
        def bind(self, _manager):
            pass

        async def cancel_chat(self, _chat_id):
            pass

        async def has_pending(self, _chat_id):
            return False

    class Provider:
        async def decide_timing(self, _request):
            assert executor.events == ["start"]
            return ResponseTiming()

        async def generate(self, _request):
            assert executor.events == ["start"]
            return LLMResponse(actions=[])

    manager = ConversationManager(Provider(), Context(), ActionQueue(executor), scheduler=Scheduler())
    await manager.handle_turn(1, 10, "привет")
    assert executor.events == ["start", "stop"]


async def test_interrupt_stops_typing_immediately():
    executor = PresenceExecutor()
    manager = ConversationManager(SimpleNamespace(), Context(), ActionQueue(executor))
    manager.generations[10] = "old"
    await manager._start_generation_typing(10, "old")
    await manager.interrupt(10)
    assert executor.events == ["start", "stop"]
