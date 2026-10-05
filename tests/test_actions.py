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


async def test_executor_failure_does_not_kill_worker_or_block_later_enqueues(caplog):
    class FailingOnceExecutor:
        def __init__(self):
            self.failed = False
            self.executed = []

        async def execute(self, item):
            if not self.failed:
                self.failed = True
                raise RuntimeError("telegram unavailable")
            self.executed.append(item.action.text or item.action.type.value)

    executor = FailingOnceExecutor()
    queue = ActionQueue(executor)
    with caplog.at_level("ERROR"):
        await queue.enqueue_many(1, "first-generation", [
            Action(type=ActionType.text, text="fails"),
            Action(type=ActionType.pause, duration=Duration.short),
        ])
        await queue._queues[1].join()
        await queue.enqueue_many(1, "second-generation", [
            Action(type=ActionType.text, text="still works"),
        ])
        await queue._queues[1].join()

    assert executor.executed == ["pause", "still works"]
    assert queue._active == {}
    assert "action_execution_failed chat_id=1 generation=first-generation action_type=text" in caplog.text
