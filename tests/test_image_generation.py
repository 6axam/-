from types import SimpleNamespace
import json

import pytest
from pydantic import ValidationError

from app.actions.models import Action, ActionType, ImageIntent, ImageKind, QueuedAction
from app.database.db import Database
from app.images import GeneratedImage, ImagePromptBuilder
from app.llm.schemas import LLMResponse
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
    action=Action(type=ActionType.image,image_intent=ImageIntent(kind="casual_photo",scene="shows she is bored",importance=.5))
    await executor.execute(QueuedAction(chat_id=1,generation_id="g",action=action))
    assert "short dark hair" in provider.prompt and "OUTFIT" in provider.prompt
    assert (await db.fetchone("SELECT status FROM generated_images"))["status"] == "sent"
    await db.close()


async def test_meme_does_not_need_appearance(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path); db=Database(f"sqlite:///{tmp_path/'b.sqlite'}"); await db.connect()
    prompt,_,_=await ImagePromptBuilder(db).build(1,ImageIntent(kind="meme",scene="a silly compiler error",importance=.2))
    assert "Immutable appearance" not in prompt
    await db.close()


async def test_every_canonical_image_kind_is_accepted_and_executed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "appearance.md").write_text("canonical appearance", encoding="utf-8")
    db = Database(f"sqlite:///{tmp_path / 'kinds.sqlite'}")
    await db.connect()

    class Provider:
        name = "fake"
        model = "fake-image"
        async def generate(self, _prompt, references=None):
            return GeneratedImage(b"image")

    class Bot:
        def __init__(self): self.sent = []
        async def send_photo(self, chat_id, *, photo, caption=None):
            self.sent.append((chat_id, photo.data, caption))
            return SimpleNamespace(message_id=100 + len(self.sent))

    bot = Bot()
    executor = TelegramActionExecutor(
        bot, db, image_provider=Provider(), image_prompts=ImagePromptBuilder(db),
        image_daily_limit=2, image_cooldown_hours=0,
    )
    for chat_id, kind in enumerate(ImageKind, start=1):
        action = Action(type=ActionType.image, image_intent=ImageIntent(kind=kind, scene="ordinary requested moment"))
        await executor.execute(QueuedAction(chat_id=chat_id, generation_id=f"g-{kind.value}", action=action))

    rows = await db.fetchall("SELECT kind,status FROM generated_images ORDER BY chat_id")
    assert [row["kind"] for row in rows] == [kind.value for kind in ImageKind]
    assert all(row["status"] == "sent" for row in rows)
    assert len(bot.sent) == len(ImageKind)
    await db.close()


def test_unknown_image_kind_is_rejected_by_schema_and_not_advertised_to_llm():
    with pytest.raises(ValidationError):
        ImageIntent(kind="creative_image", scene="unsupported")
    with pytest.raises(ValidationError):
        LLMResponse.model_validate_json('{"actions":[{"type":"image","image_intent":{"kind":"photo_of_something","scene":"unsupported"}}]}')

    schema = json.dumps(ImageIntent.model_json_schema())
    prompt = open("prompts/response.md", encoding="utf-8").read()
    assert "creative_image" not in schema and "photo_of_something" not in schema
    assert "creative_image" not in prompt and "photo_of_something" not in prompt
    assert set(ImagePromptBuilder.kinds) == {kind.value for kind in ImageKind}
    for kind in ImageKind:
        assert kind.value in prompt
