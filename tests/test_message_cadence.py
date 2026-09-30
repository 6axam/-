import asyncio

from app.actions.models import Action, ActionType
from app.actions.queue import ActionQueue


class RecordingExecutor:
    def __init__(self): self.types = []
    async def execute(self, item): self.types.append(item.action.type)


async def test_consecutive_text_actions_get_a_cancelable_pause():
    executor = RecordingExecutor()
    queue = ActionQueue(executor)
    await queue.enqueue_many(1, "generation", [
        Action(type=ActionType.text, text="первая мысль"),
        Action(type=ActionType.text, text="вторая мысль"),
    ])
    await asyncio.sleep(.02)
    assert executor.types == [ActionType.text, ActionType.pause, ActionType.text]
