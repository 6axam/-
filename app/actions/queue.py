import asyncio
from collections import defaultdict
from app.actions.models import Action, ActionType, Duration, QueuedAction


class ActionQueue:
    """One sequential worker per chat; a new generation cancels pending old work."""
    def __init__(self, executor):
        self.executor = executor
        self._queues: dict[int, asyncio.Queue[QueuedAction]] = defaultdict(asyncio.Queue)
        self._workers: dict[int, asyncio.Task] = {}
        self._cancelled: set[str] = set()
        self._active: dict[int, tuple[str, asyncio.Task, bool]] = {}

    async def enqueue_many(self, chat_id: int, generation_id: str, actions):
        previous = None
        for action in actions:
            # Consecutive text actions are independent Telegram messages. Add a
            # short cancelable cadence pause unless the model already supplied
            # an explicit pause between them.
            if previous and previous.type == ActionType.text and action.type == ActionType.text:
                await self._queues[chat_id].put(QueuedAction(chat_id=chat_id, generation_id=generation_id, action=Action(type=ActionType.pause, duration=Duration.short, text=action.text, cancelable=True, automatic=True)))
            await self._queues[chat_id].put(QueuedAction(chat_id=chat_id, generation_id=generation_id, action=action))
            previous = action
        if chat_id not in self._workers or self._workers[chat_id].done():
            self._workers[chat_id] = asyncio.create_task(self._work(chat_id))

    def cancel_generation(self, generation_id: str) -> None:
        self._cancelled.add(generation_id)
        # A pause/typing action that has not produced a Telegram message yet is
        # safe to interrupt immediately; sent messages are never retracted.
        for chat_id, (active_generation, task, cancelable) in list(self._active.items()):
            if active_generation == generation_id and cancelable and not task.done():
                task.cancel()

    def is_busy(self, chat_id: int) -> bool:
        worker = self._workers.get(chat_id)
        return bool(worker and not worker.done())

    async def _work(self, chat_id: int):
        queue = self._queues[chat_id]
        while not queue.empty():
            item = await queue.get()
            if item.generation_id not in self._cancelled or not item.action.cancelable:
                task = asyncio.create_task(self.executor.execute(item))
                self._active[chat_id] = (item.generation_id, task, item.action.cancelable)
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                finally:
                    self._active.pop(chat_id, None)
            queue.task_done()
