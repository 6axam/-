from types import SimpleNamespace

from app.actions.models import Action, ActionType, QueuedAction
from app.character.manager import EmotionalStateManager
from app.conversation.context import ContextBuilder
from app.conversation.manager import ConversationManager
from app.database.db import Database
from app.telegram.executor import TelegramActionExecutor


class ReactionBot:
    def __init__(self):
        self.calls = []

    async def set_message_reaction(self, chat_id, message_id, reaction):
        self.calls.append((chat_id, message_id, reaction[0].emoji))


class NoopQueue:
    def cancel_generation(self, _generation):
        pass

    def is_busy(self, _chat_id):
        return False


async def test_reaction_state_and_context_signal(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'bot.sqlite'}")
    await db.connect()
    await db.ensure_user(7, "maksim")
    await db.record_message(chat_id=10, telegram_message_id=22, user_id=7, sender="assistant", kind="text", text="ну да")

    await db.record_reaction(chat_id=10, telegram_message_id=22, actor="user", actor_user_id=7, emoji="❤️")
    signals = await db.recent_reaction_signals(10, 7)
    assert [row["emoji"] for row in signals] == ["❤️"]

    _system, context = await ContextBuilder(db).build(7, 10, "дальше")
    assert "Максим поставил ❤️" in context
    assert "Не отвечай на него отдельно" in context

    await db.record_reaction(chat_id=10, telegram_message_id=22, actor="user", actor_user_id=7, emoji="❤️", active=False)
    assert await db.recent_reaction_signals(10, 7) == []
    await db.close()


async def test_warm_reaction_changes_state_without_generating_a_turn(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'bot.sqlite'}")
    await db.connect()
    emotions = EmotionalStateManager(db)
    before = await emotions.get()
    manager = ConversationManager(SimpleNamespace(), SimpleNamespace(), NoopQueue(), emotional_state=emotions)

    await manager.observe_reaction(10, "🔥")
    after = await emotions.get()
    assert after["conversation_interest"] > before["conversation_interest"]
    assert manager.generations == {}
    await db.close()


async def test_outgoing_reaction_is_sent_and_persisted(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'bot.sqlite'}")
    await db.connect()
    bot = ReactionBot()
    executor = TelegramActionExecutor(bot, db)
    await executor.execute(QueuedAction(chat_id=10, generation_id="g", action=Action(type=ActionType.reaction, emoji="❤️", target_message_id=55)))

    assert bot.calls == [(10, 55, "❤️")]
    row = await db.fetchone("SELECT actor,emoji,active FROM message_reactions WHERE chat_id=? AND telegram_message_id=?", (10, 55))
    assert dict(row) == {"actor": "assistant", "emoji": "❤️", "active": 1}
    await db.close()
