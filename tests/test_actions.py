import asyncio
from types import SimpleNamespace

from app.actions.models import Action, ActionType, Duration
from app.actions.queue import ActionQueue


class FakeExecutor:
    def __init__(self, pause_seconds=0):
        self.executed = []
        self.pause_seconds = pause_seconds

    async def execute(self, item):
        if item.action.type == ActionType.pause:
            await asyncio.sleep(self.pause_seconds)
        self.executed.append(item.action.text or item.action.type.value)


async def test_actions_execute_sequentially():
    executor = FakeExecutor()
    queue = ActionQueue(executor)
    await queue.enqueue_many(1, "gen", [
        Action(type=ActionType.text, text="first"),
        Action(type=ActionType.pause, duration=Duration.short),
        Action(type=ActionType.text, text="second"),
    ])
    await asyncio.sleep(.02)
    assert executor.executed == ["first", "pause", "second"]


async def test_interruption_cancels_pending_cancelable_actions():
    executor = FakeExecutor(pause_seconds=.2)
    queue = ActionQueue(executor)
    await queue.enqueue_many(1, "old", [
        Action(type=ActionType.text, text="sent"),
        Action(type=ActionType.pause, duration=Duration.long),
        Action(type=ActionType.text, text="must not send"),
    ])
    await asyncio.sleep(.02)
    queue.cancel_generation("old")
    await asyncio.sleep(.05)
    assert executor.executed == ["sent"]
