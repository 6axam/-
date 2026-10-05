from types import SimpleNamespace
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.actions.models import Action, ActionType, ImageIntent, ImageKind, QueuedAction
from app.actions.queue import ActionQueue
from app.database.db import Database
from app.images import GeneratedImage, ImagePromptBuilder
from app.llm.schemas import LLMResponse
from app.telegram.executor import TelegramActionExecutor


class FixedDateTime(datetime):
    current = datetime(2026, 9, 28, 10, tzinfo=ZoneInfo("Europe/Kyiv"))

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current


class OutfitRng:
    def __init__(self, college_outfits):
        self.college_outfits = iter(college_outfits)
        self.outfit_choices = 0

    def choice(self, values):
        if values == ImagePromptBuilder.outfits["college"]:
            self.outfit_choices += 1
            return next(self.college_outfits)
        return values[0]


async def visual_builder(tmp_path, monkeypatch, rng):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompts").mkdir(exist_ok=True)
    (tmp_path / "prompts" / "appearance.md").write_text("canonical appearance", encoding="utf-8")
    (tmp_path / "assets" / "anya").mkdir(parents=True, exist_ok=True)
    (tmp_path / "assets" / "anya" / "reference.jpg").write_bytes(b"reference-image")
    db = Database(f"sqlite:///{tmp_path / 'visual.sqlite'}")
    await db.connect()
    import app.images as images
    monkeypatch.setattr(images, "datetime", FixedDateTime)
    return db, ImagePromptBuilder(db, rng=rng)


async def test_self_photo_pipeline_persists_visual_and_image_metadata(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "appearance.md").write_text(
        "The attached reference is the only source of stable physical identity.", encoding="utf-8"
    )
    (tmp_path / "assets" / "anya").mkdir(parents=True)
    (tmp_path / "assets" / "anya" / "reference.jpg").write_bytes(b"reference-image")
    db = Database(f"sqlite:///{tmp_path / 'bot.sqlite'}"); await db.connect()
    class Provider:
        name="fake"; model="fake-image"; supports_references=True
        async def generate(self, prompt, references=None):
            self.prompt, self.references = prompt, references
            return GeneratedImage(b"image")
    class Bot:
        def __init__(self): self.messages = []
        async def send_photo(self, *_args, **kwargs):
            assert kwargs["photo"].data == b"image"
            assert kwargs["photo"].filename == "anya.jpg"
            return SimpleNamespace(message_id=99)
        async def send_message(self, _chat_id, text):
            self.messages.append(text); return SimpleNamespace(message_id=100)
    class Lifecycle:
        def __init__(self): self.calls = []
        async def on_bot_message(self, chat_id): self.calls.append(chat_id)
    provider=Provider(); bot=Bot(); lifecycle=Lifecycle(); executor=TelegramActionExecutor(bot, db, lifecycle=lifecycle, image_provider=provider, image_prompts=ImagePromptBuilder(db), image_daily_limit=2, image_cooldown_hours=1)
    action=Action(type=ActionType.image,image_intent=ImageIntent(kind="casual_photo",scene="shows she is bored",importance=.5))
    await executor.execute(QueuedAction(chat_id=1,generation_id="g",action=action))
    assert "REFERENCE IDENTITY" in provider.prompt and "OUTFIT" in provider.prompt
    assert "only source of stable physical identity" in provider.prompt
    assert "short dark hair" not in provider.prompt and "green eyes" not in provider.prompt
    assert provider.references == [b"reference-image"]
    assert bot.messages == [] and lifecycle.calls == [1]
    assert (await db.fetchone("SELECT status FROM generated_images"))["status"] == "sent"
    await db.close()


def test_repository_identity_prompt_uses_reference_instead_of_fixed_traits():
    prompt = Path("prompts/appearance.md").read_text(encoding="utf-8")
    assert "only source of Anya's stable physical identity" in prompt
    assert "current situation: mood, expression, time, place, activity, weather" in prompt
    for obsolete_trait in (
        "серо-зелёные",
        "почти чёрные волосы",
        "серебряным крестом",
        "ногти обычно чёрные",
    ):
        assert obsolete_trait not in prompt.casefold()


async def test_meme_does_not_need_appearance(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path); db=Database(f"sqlite:///{tmp_path/'b.sqlite'}"); await db.connect()
    prompt,_,_=await ImagePromptBuilder(db).build(1,ImageIntent(kind="meme",scene="a silly compiler error",importance=.2))
    assert "Immutable appearance" not in prompt
    await db.close()


@pytest.mark.parametrize("kind", ["object_photo", "environment_photo", "meme"])
async def test_non_identity_images_do_not_require_reference(tmp_path, monkeypatch, kind):
    monkeypatch.chdir(tmp_path)
    db=Database(f"sqlite:///{tmp_path/'non-identity.sqlite'}"); await db.connect()
    _prompt, _visual, references = await ImagePromptBuilder(db).build(
        1, ImageIntent(kind=kind, scene="ordinary requested subject"),
    )
    assert references == []
    await db.close()


async def test_self_present_image_requires_existing_canonical_reference(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "appearance.md").write_text("reference-only identity", encoding="utf-8")
    db=Database(f"sqlite:///{tmp_path/'missing-reference.sqlite'}"); await db.connect()
    with pytest.raises(FileNotFoundError, match="canonical identity reference is missing"):
        await ImagePromptBuilder(db).build(1, ImageIntent(kind="casual_photo", scene="ordinary moment"))
    await db.close()


async def test_visual_state_is_identical_for_two_prompts_in_one_period(tmp_path, monkeypatch):
    rng = OutfitRng(["first college outfit", "second college outfit"])
    db, builder = await visual_builder(tmp_path, monkeypatch, rng)
    intent = ImageIntent(kind="casual_photo", scene="ordinary moment")

    _, first, _ = await builder.build(1, intent)
    _, second, _ = await builder.build(1, intent)

    assert first == second
    assert rng.outfit_choices == 1
    await db.close()


async def test_self_photo_prompt_has_independent_pose_action_framing_and_safety_rules(tmp_path, monkeypatch):
    db, builder = await visual_builder(tmp_path, monkeypatch, OutfitRng(["ordinary college outfit"]))
    prompt, _state, _refs = await builder.build(
        1, ImageIntent(kind="front_selfie", scene="quick photo in a college hallway")
    )

    for block in ("PHOTO TYPE", "POSE", "MICRO-ACTION", "CAMERA / FRAMING", "NATURAL POSE / REALISM"):
        assert block in prompt
    assert "impossible shoulder angles" in prompt
    assert "awkward full-body selfie distortion" in prompt
    assert "full-body mirror framing" not in prompt
    await db.close()


async def test_existing_sqlite_visual_state_survives_new_builder_and_rng(tmp_path, monkeypatch):
    first_rng = OutfitRng(["saved college outfit"])
    db, first_builder = await visual_builder(tmp_path, monkeypatch, first_rng)
    intent = ImageIntent(kind="casual_photo", scene="ordinary moment")
    _, saved, _ = await first_builder.build(1, intent)

    new_rng = OutfitRng(["must not be chosen"])
    restarted_builder = ImagePromptBuilder(db, rng=new_rng)
    _, restored, _ = await restarted_builder.build(1, intent)

    assert restored == saved
    assert new_rng.outfit_choices == 0
    assert (await db.fetchone("SELECT clothing_context FROM visual_state WHERE chat_id=1"))["clothing_context"] == "saved college outfit"
    await db.close()


async def test_new_period_may_create_a_new_visual_state(tmp_path, monkeypatch):
    rng = OutfitRng(["monday college outfit", "tuesday college outfit"])
    db, builder = await visual_builder(tmp_path, monkeypatch, rng)
    intent = ImageIntent(kind="casual_photo", scene="ordinary moment")
    _, first, _ = await builder.build(1, intent)
    first_key = (await db.fetchone("SELECT period_key FROM visual_state WHERE chat_id=1"))["period_key"]

    FixedDateTime.current += timedelta(days=1)
    try:
        _, second, _ = await builder.build(1, intent)
    finally:
        FixedDateTime.current -= timedelta(days=1)
    second_key = (await db.fetchone("SELECT period_key FROM visual_state WHERE chat_id=1"))["period_key"]

    assert first["clothing"] == "monday college outfit"
    assert second["clothing"] == "tuesday college outfit"
    assert first_key != second_key
    assert rng.outfit_choices == 2
    await db.close()


async def test_every_canonical_image_kind_is_accepted_and_executed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "appearance.md").write_text("canonical appearance", encoding="utf-8")
    (tmp_path / "assets" / "anya").mkdir(parents=True)
    (tmp_path / "assets" / "anya" / "reference.jpg").write_bytes(b"reference-image")
    db = Database(f"sqlite:///{tmp_path / 'kinds.sqlite'}")
    await db.connect()

    class Provider:
        name = "fake"
        model = "fake-image"
        supports_references = True
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


async def test_provider_without_reference_support_is_blocked_before_api_call(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "appearance.md").write_text("reference-only identity", encoding="utf-8")
    (tmp_path / "assets" / "anya").mkdir(parents=True)
    (tmp_path / "assets" / "anya" / "reference.jpg").write_bytes(b"reference-image")
    db=Database(f"sqlite:///{tmp_path/'unsupported.sqlite'}"); await db.connect()

    class Provider:
        name="no-reference"; model="fake"; supports_references=False
        def __init__(self): self.calls = 0
        async def generate(self, _prompt, references=None):
            self.calls += 1
            raise AssertionError("API must not be called")
    class Bot:
        def __init__(self): self.messages=[]
        async def send_message(self, _chat_id, text):
            self.messages.append(text); return SimpleNamespace(message_id=77)

    provider, bot = Provider(), Bot()
    executor=TelegramActionExecutor(bot, db, image_provider=provider, image_prompts=ImagePromptBuilder(db), image_cooldown_hours=0)
    await executor.execute(QueuedAction(
        chat_id=1, generation_id="unsupported",
        action=Action(type=ActionType.image, image_intent=ImageIntent(kind="casual_photo", scene="ordinary moment")),
    ))

    assert provider.calls == 0
    assert bot.messages == [executor.image_failure_fallback]
    await db.close()


async def test_image_failure_fallback_is_once_for_image_only_and_suppressed_by_text(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "appearance.md").write_text("reference-only identity", encoding="utf-8")
    (tmp_path / "assets" / "anya").mkdir(parents=True)
    (tmp_path / "assets" / "anya" / "reference.jpg").write_bytes(b"reference-image")
    db=Database(f"sqlite:///{tmp_path/'fallback.sqlite'}"); await db.connect()

    class Provider:
        name="failing"; model="fake"; supports_references=True
        async def generate(self, _prompt, references=None): raise RuntimeError("generation failed")
    class Bot:
        def __init__(self): self.messages=[]; self.next_id=100
        async def send_chat_action(self, *_args): pass
        async def send_message(self, _chat_id, text, **_kwargs):
            self.next_id += 1; self.messages.append(text); return SimpleNamespace(message_id=self.next_id)
    class Lifecycle:
        def __init__(self): self.calls=[]
        async def on_bot_message(self, chat_id): self.calls.append(chat_id)

    bot, lifecycle = Bot(), Lifecycle()
    executor=TelegramActionExecutor(bot, db, lifecycle=lifecycle, image_provider=Provider(), image_prompts=ImagePromptBuilder(db), image_cooldown_hours=0)
    executor.timing.typing_seconds = lambda _text: 0
    queue=ActionQueue(executor)
    image=Action(type=ActionType.image, image_intent=ImageIntent(kind="casual_photo", scene="ordinary moment"))
    await queue.enqueue_many(1, "image-only", [image, image.model_copy(deep=True)])
    await queue._queues[1].join()
    assert bot.messages == [executor.image_failure_fallback]

    bot.messages.clear(); lifecycle.calls.clear()
    await queue.enqueue_many(1, "text-and-image", [Action(type=ActionType.text, text="щас скину"), image])
    await queue._queues[1].join()
    assert bot.messages == ["щас скину"]
    assert lifecycle.calls == [1]
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
        assert kind.value not in prompt
