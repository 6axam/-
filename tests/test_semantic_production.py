import asyncio
import json
from random import Random
from types import SimpleNamespace

from app.actions.models import StickerIntent
from app.actions.queue import ActionQueue
from app.conversation.context import ContextBuilder
from app.conversation.manager import ConversationManager
from app.database.db import Database
from app.llm.schemas import StickerSemantics
from app.llm.openai_provider import OpenAICompatibleProvider
from app.llm.schemas import ImageContent, LLMRequest, LLMResponse
from app.media.analyzer import MediaDescriptionWorker
from app.stickers.analyzer import StickerAnalysisWorker
from app.stickers.manager import StickerManager
from app.stickers.retriever import EmbeddingProvider, EmbeddingProviderUnavailable, FakeEmbeddingProvider


async def db_for(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'production.sqlite'}"); await db.connect(); return db


def manager_for(db, **kwargs): return StickerManager(db, FakeEmbeddingProvider(), **kwargs)


async def semantic(manager, unique, visual, meanings, intensity=.5):
    await manager.register_incoming(SimpleNamespace(file_id=f"file-{unique}", file_unique_id=unique, emoji="", set_name=None, is_animated=False, is_video=False))
    row = await manager.db.fetchone("SELECT id FROM stickers WHERE file_unique_id=?", (unique,))
    await manager.save_semantics(row["id"], StickerSemantics(visual_description=visual, meanings=meanings, intensity=intensity), analyzer_model="fake", analysis_version="test")
    return row["id"]


async def test_stale_hash_embedding_is_lazily_reembedded(tmp_path):
    db = await db_for(tmp_path); manager = manager_for(db); sticker_id = await semantic(manager, "a", "cat facepalm", ["facepalm", "cringe"])
    await db.execute("UPDATE sticker_embeddings SET embedding_model='hashing-semantic-v1' WHERE sticker_id=?", (sticker_id,))
    await manager.candidates("глупая ошибка", chat_id=1)
    row = await db.fetchone("SELECT embedding_model FROM sticker_embeddings WHERE sticker_id=?", (sticker_id,))
    assert row["embedding_model"] == "fake-semantic-v1"
    await db.close()


async def test_intensity_and_time_aware_outgoing_repeat_penalty(tmp_path):
    db = await db_for(tmp_path); manager = manager_for(db)
    calm = await semantic(manager, "calm", "cat facepalm", ["facepalm", "failure"], .2)
    loud = await semantic(manager, "loud", "cat facepalm", ["facepalm", "failure"], .95)
    assert (await manager.candidates("facepalm failure", chat_id=1, intensity=.2))[0]["id"] == calm
    await manager.record_usage(calm, 1, "incoming")
    assert (await manager.candidates("facepalm failure", chat_id=1, intensity=.2))[0]["id"] == calm
    await manager.record_usage(calm, 1, "outgoing")
    penalized = next(item for item in await manager.candidates("facepalm failure", chat_id=1, intensity=.2) if item["id"] == calm)
    assert penalized["score_parts"]["repeat_penalty"] >= .16
    await db.execute("UPDATE sticker_usage SET used_at=datetime('now','-25 hours') WHERE sticker_id=?", (calm,))
    old = next(item for item in await manager.candidates("facepalm failure", chat_id=1, intensity=.2) if item["id"] == calm)
    assert old["score_parts"]["repeat_penalty"] == 0
    await db.close()


async def test_weighted_top_only_varies_when_scores_are_close(tmp_path):
    db = await db_for(tmp_path); manager = manager_for(db, selection_strategy="weighted_top", rng=Random(1))
    first = {"id": 1, "score": .90}; second = {"id": 2, "score": .70}
    assert manager.retriever.choose([first, second])["id"] == 1
    close = [{"id": 1, "score": .90}, {"id": 2, "score": .89}, {"id": 3, "score": .88}]
    assert manager.retriever.choose(close)["id"] in {1, 2, 3}
    await db.close()


async def test_semantics_and_photo_description_appear_in_future_context(tmp_path):
    db = await db_for(tmp_path); manager = manager_for(db); sticker_id = await semantic(manager, "s", "cat covers face", ["facepalm", "disbelief"])
    await db.record_message(chat_id=1, telegram_message_id=1, sender="user", user_id=7, kind="sticker", sticker_file_id="file-s")
    await db.record_message(chat_id=1, telegram_message_id=2, sender="user", user_id=7, kind="photo", text="[photo]")
    media_id = await db.record_media(chat_id=1, telegram_message_id=2, file_id="photo", file_unique_id="photo-u", media_type="photo", mime_type="image/jpeg", local_cache_path=None, byte_size=None)
    await db.execute("INSERT INTO media_descriptions(media_id,description,model) VALUES(?,?,?)", (media_id, "reactor with blue lighting", "fake"))
    _, context = await ContextBuilder(db, recent_media_hours=24).build(7, 1, "ну как?")
    assert "cat covers face" in context and "reactor with blue lighting" in context
    await db.close()


async def test_sticker_source_cache_prevents_second_download(tmp_path):
    class Bot:
        def __init__(self): self.calls = 0
        async def get_file(self, _): self.calls += 1; return SimpleNamespace(file_path="x", file_size=3)
        async def download_file(self, _, destination): destination.write(b"RIFFxxxxWEBPraw")
    class Extractor:
        def extract(self, *_): return ["frame"]
    settings = SimpleNamespace(sticker_analysis_max_frames=1, media_cache_dir=str(tmp_path), max_animation_bytes=100, max_sticker_bytes=100)
    db = await db_for(tmp_path); manager = manager_for(db); await manager.register_incoming(SimpleNamespace(file_id="x", file_unique_id="u", emoji="", set_name=None, is_animated=False, is_video=False))
    sticker_id = (await db.fetchone("SELECT id FROM stickers WHERE file_unique_id='u'"))["id"]
    bot = Bot(); worker = StickerAnalysisWorker(db, bot, manager, SimpleNamespace(), settings, extractor=Extractor())
    assert await worker.prepare_frames(sticker_id) == ["frame"]
    worker._frame_tasks.clear()
    assert await worker.prepare_frames(sticker_id) == ["frame"]
    assert bot.calls == 1
    await db.close()


async def test_live_bug_webm_cached_as_webp_is_reclassified_without_redownload(tmp_path):
    """Telegram metadata once declared this whole video pack static in production."""
    class Bot:
        async def get_file(self, _):
            raise AssertionError("valid cached bytes must not be downloaded again")

    class Extractor:
        def __init__(self): self.seen_type = None
        def extract(self, _data, sticker_type):
            self.seen_type = sticker_type
            return ["video-frame"]

    settings = SimpleNamespace(sticker_analysis_max_frames=1, media_cache_dir=str(tmp_path / "cache"), max_animation_bytes=100, max_sticker_bytes=100)
    db = await db_for(tmp_path); manager = manager_for(db)
    await manager.register_incoming(SimpleNamespace(file_id="video-file", file_unique_id="video-u", emoji="", set_name=None, is_animated=False, is_video=False))
    sticker = await db.fetchone("SELECT * FROM stickers WHERE file_unique_id='video-u'")
    old_path = tmp_path / "cache" / "stickers" / "video-u.webp"; old_path.parent.mkdir(parents=True); old_path.write_bytes(b"\x1a\x45\xdf\xa3webm payload")
    await db.execute("UPDATE stickers SET local_cache_path=? WHERE id=?", (str(old_path), sticker["id"]))
    extractor = Extractor(); worker = StickerAnalysisWorker(db, Bot(), manager, SimpleNamespace(), settings, extractor=extractor)

    assert await worker.prepare_frames(sticker["id"]) == ["video-frame"]
    repaired = await db.fetchone("SELECT sticker_type,cached_sticker_type,local_cache_path FROM stickers WHERE id=?", (sticker["id"],))
    assert extractor.seen_type == "video"
    assert repaired["sticker_type"] == repaired["cached_sticker_type"] == "video"
    assert repaired["local_cache_path"].endswith("video-u.webm")
    assert not old_path.exists()
    await db.close()


async def test_photo_description_worker_persists_description(tmp_path):
    class Provider:
        model = "fake"
        async def describe_image(self, _): return "a small reactor"
    db = await db_for(tmp_path); path = tmp_path / "p.jpg"; path.write_bytes(b"image")
    media_id = await db.record_media(chat_id=1, telegram_message_id=1, file_id="p", file_unique_id="pu", media_type="photo", mime_type="image/jpeg", local_cache_path=str(path), byte_size=5)
    await db.queue_media_description(media_id)
    worker = MediaDescriptionWorker(db, Provider()); await worker.process_one()
    assert (await db.fetchone("SELECT description FROM media_descriptions WHERE media_id=?", (media_id,)))["description"] == "a small reactor"
    await db.close()


async def test_delayed_generation_keeps_pending_photo_bytes(tmp_path):
    class Provider:
        supports_vision = True
        def __init__(self): self.request = None
        async def generate(self, request): self.request = request; return LLMResponse()
    class Media:
        async def inputs_for_messages(self, _, ids):
            assert ids == [8]
            return [ImageContent(data=b"photo", mime_type="image/jpeg")]
    class Scheduler:
        def bind(self, _): pass
        async def is_current(self, _): return True
        async def complete(self, _): pass
    db = await db_for(tmp_path); await db.record_message(chat_id=3, telegram_message_id=8, sender="user", user_id=4, kind="photo", text="[photo]")
    provider = Provider(); manager = ConversationManager(provider, ContextBuilder(db), ActionQueue(SimpleNamespace(execute=lambda _: None)), scheduler=Scheduler(), media=Media())
    await manager.handle_scheduled({"id": 1, "chat_id": 3, "generation_id": "g"})
    assert provider.request.user_content[0].data == b"photo"
    await db.close()


async def test_analyzer_invalid_json_repairs_once():
    calls = 0
    async def handler(_):
        nonlocal calls; calls += 1
        content = "broken" if calls == 1 else json.dumps({"visual_description": "cat", "characters": [], "emotions": [], "meanings": ["test"], "usage": [], "intensity": .5})
        return __import__("httpx").Response(200, json={"choices": [{"message": {"content": content}}]})
    provider = OpenAICompatibleProvider("key", "model", transport=__import__("httpx").MockTransport(handler), retries=0)
    result = await provider.analyze_sticker([ImageContent(data=b"png", mime_type="image/png")])
    assert result.visual_description == "cat" and calls == 2


def test_prompt_does_not_frame_backend_as_character_world():
    prompt = open("prompts/response.md", encoding="utf-8").read().lower()
    assert "available stickers" not in prompt and "candidate list" not in prompt and "database" not in prompt


async def test_runtime_system_context_has_no_backend_vocabulary(tmp_path):
    db = await db_for(tmp_path); system, _ = await ContextBuilder(db).build(1, 1, "привет")
    lowered = system.lower()
    for forbidden in ("available stickers", "candidate list", "database", "vision analyzer", "analysis failed"):
        assert forbidden not in lowered
    await db.close()


def test_unknown_sticker_frames_respect_multiple_image_capability():
    request = LLMRequest(system="s", context="c", user_turn="unknown sticker", user_content=[ImageContent(data=b"one", mime_type="image/png"), ImageContent(data=b"two", mime_type="image/png")])
    multi = OpenAICompatibleProvider("k", "m", supports_vision=True, supports_multiple_images=True)
    single = OpenAICompatibleProvider("k", "m", supports_vision=True, supports_multiple_images=False)
    assert len(multi._user_content(request)) == 3
    assert len(single._user_content(request)) == 2
