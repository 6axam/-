import asyncio
from types import SimpleNamespace

from app.actions.queue import ActionQueue
from app.conversation.context import ContextBuilder
from app.conversation.manager import ConversationManager
from app.database.db import Database
from app.llm.schemas import LLMResponse
from app.actions.models import Action, ActionType, Duration
from app.telegram.executor import TelegramActionExecutor


class FakeBot:
    def __init__(self): self.typing = 0; self.sent = []
    async def send_chat_action(self, *_args): self.typing += 1
    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=900 + len(self.sent))


class FakeProvider:
    async def generate(self, _request):
        return LLMResponse(actions=[
            Action(type=ActionType.text, text="бля"),
            Action(type=ActionType.pause, duration=Duration.short),
            Action(type=ActionType.text, text="а ошибка какая?"),
        ])


async def test_offline_telegram_llm_flow_persists_outgoing_messages(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'bot.sqlite'}")
    await db.connect()
    await db.ensure_user(1, "owner")
    await db.record_message(chat_id=100, telegram_message_id=1, user_id=1, sender="user", kind="text", text="не работает")
    bot = FakeBot()
    executor = TelegramActionExecutor(bot, db)
    executor.timing.typing_seconds = lambda _text: 0
    executor.timing.pause = lambda _duration: 0
    manager = ConversationManager(FakeProvider(), ContextBuilder(db), ActionQueue(executor))
    await manager.handle_turn(1, 100, "не работает")
    await asyncio.sleep(.03)
    assert bot.sent == [(100, "бля"), (100, "а ошибка какая?")]
    rows = await db.fetchall("SELECT text FROM messages WHERE sender='assistant' ORDER BY id")
    assert [row["text"] for row in rows] == ["бля", "а ошибка какая?"]
    # One typing event starts immediately when the LLM request begins, then
    # each outgoing text message has its own typing beat.
    assert bot.typing == 3
    await db.close()
