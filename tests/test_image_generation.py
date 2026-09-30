from types import SimpleNamespace

from app.actions.models import Action, ActionType, ImageIntent, QueuedAction
from app.database.db import Database
from app.images import GeneratedImage, ImagePromptBuilder
from app.telegram.executor import TelegramActionExecutor


async def test_self_photo_pipeline_persists_visual_and_image_metadata(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompts").mkdir(); (tmp_path / "prompts" / "appearance.md").write_text("short dark hair, green eyes", encoding="utf-8")
    db = Database(f"sqlite:///{tmp_path / 'bot.sqlite'}"); await db.connect()
    class Provider:
        name="fake"; model="fake-image"
        async def generate(self, prompt, references=None): self.prompt=prompt; return GeneratedImage(b"image")
    class Bot:
        async def send_photo(self, *_args, **kwargs):
            assert kwargs["photo"].data == b"image"
            assert kwargs["photo"].filename == "anya.jpg"
            return SimpleNamespace(message_id=99)
    provider=Provider(); executor=TelegramActionExecutor(Bot(), db, image_provider=provider, image_prompts=ImagePromptBuilder(db), image_daily_limit=2, image_cooldown_hours=1)
    action=Action(type=ActionType.image,image_intent=ImageIntent(kind="casual_self_photo",scene="shows she is bored",importance=.5))
    await executor.execute(QueuedAction(chat_id=1,generation_id="g",action=action))
    assert "short dark hair" in provider.prompt and "OUTFIT" in provider.prompt
    assert (await db.fetchone("SELECT status FROM generated_images"))["status"] == "sent"
    await db.close()


async def test_meme_does_not_need_appearance(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path); db=Database(f"sqlite:///{tmp_path/'b.sqlite'}"); await db.connect()
    prompt,_,_=await ImagePromptBuilder(db).build(1,ImageIntent(kind="meme",scene="a silly compiler error",importance=.2))
    assert "Immutable appearance" not in prompt
    await db.close()
