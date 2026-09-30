import asyncio
import io
import subprocess
import tempfile
from types import SimpleNamespace

from PIL import Image

from app.actions.models import Action, ActionType, StickerIntent
from app.actions.queue import ActionQueue
from app.conversation.context import ContextBuilder
from app.conversation.manager import ConversationManager
from app.database.db import Database
from app.llm.schemas import ImageContent, LLMResponse, StickerSemantics
from app.llm.openai_provider import OpenAICompatibleProvider
from app.llm.schemas import LLMRequest
from app.media.manager import MediaManager
from app.stickers.analyzer import StickerAnalysisWorker
from app.stickers.frames import StickerFrameExtractor
from app.stickers.manager import StickerManager
from app.stickers.retriever import FakeEmbeddingProvider
from app.telegram.executor import TelegramActionExecutor


async def make_db(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'vision.sqlite'}"); await db.connect(); return db

def make_stickers(db): return StickerManager(db, FakeEmbeddingProvider())


def sticker(file_id, unique, *, animated=False, video=False, set_name="cats"):
    return SimpleNamespace(file_id=file_id, file_unique_id=unique, emoji="", is_animated=animated, is_video=video, set_name=set_name)


async def save(manager, sticker_id, visual, meanings):
    await manager.save_semantics(sticker_id, StickerSemantics(visual_description=visual, meanings=meanings), analyzer_model="fake", analysis_version="test")


async def test_photo_reaches_conversation_as_generic_multimodal_content(tmp_path):
    class Provider:
        supports_vision = True
        def __init__(self): self.request = None
        async def generate(self, request): self.request = request; return LLMResponse()
    db = await make_db(tmp_path); provider = Provider()
    manager = ConversationManager(provider, ContextBuilder(db), ActionQueue(SimpleNamespace(execute=lambda _: None)))
    image = ImageContent(data=b"not-telegram-object", mime_type="image/png", source="telegram_photo")
    await manager.handle_turn(1, 10, "смотри", user_content=[image])
    assert provider.request.user_content == [image]
    await db.close()


async def test_media_caption_is_stored_and_vision_disabled_fallback_is_safe(tmp_path):
    class Bot:
        async def get_file(self, _): return SimpleNamespace(file_path="photo", file_size=3)
        async def download_file(self, _, destination): destination.write(b"abc")
    db = await make_db(tmp_path); media = MediaManager(db, Bot(), str(tmp_path / "cache"), 100)
    await db.record_message(chat_id=4, telegram_message_id=9, sender="user", user_id=1, kind="photo", text="мой кот")
    await media.ingest_photo(chat_id=4, message_id=9, photo=SimpleNamespace(file_id="f", file_unique_id="u"))
    assert (await db.fetchone("SELECT text FROM messages WHERE telegram_message_id=9"))["text"] == "мой кот"
    assert await media.inputs_for_messages(4, [9])
    await db.close()


def test_provider_capability_turns_generic_image_into_data_url_or_safe_text():
    request = LLMRequest(system="s", context="c", user_turn="u", user_content=[ImageContent(data=b"abc", mime_type="image/png")])
    vision = OpenAICompatibleProvider("k", "m", supports_vision=True)
    content = vision._user_content(request)
    assert isinstance(content, list) and content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    disabled = OpenAICompatibleProvider("k", "m", supports_vision=False)
    assert isinstance(disabled._user_content(request), str)


async def test_pack_import_known_sticker_does_not_requeue_and_current_is_priority(tmp_path):
    db = await make_db(tmp_path); manager = make_stickers(db)
    one, two = sticker("a", "ua"), sticker("b", "ub")
    pack = SimpleNamespace(name="cats", title="Cats", stickers=[one, two])
    await manager.import_set(pack, current_file_unique_id="ub")
    jobs = await db.fetchall("SELECT s.file_unique_id,j.priority FROM sticker_analysis_jobs j JOIN stickers s ON s.id=j.sticker_id ORDER BY j.priority DESC")
    assert [row["file_unique_id"] for row in jobs] == ["ub", "ua"]
    await manager.import_set(pack, current_file_unique_id="ub")
    assert len(await db.fetchall("SELECT * FROM sticker_analysis_jobs")) == 2
    await db.close()


async def test_known_sticker_semantics_are_persistent_and_retrieved(tmp_path):
    db = await make_db(tmp_path); manager = make_stickers(db)
    await manager.register_incoming(sticker("kiss", "uk", set_name=None)); row = await db.fetchone("SELECT id FROM stickers WHERE file_unique_id='uk'")
    await save(manager, row["id"], "cute cat sends kiss", ["affection", "kiss", "warmth"])
    assert "affection" in (await manager.semantic_for_unique_id("uk"))
    await db.close()
    db = Database(f"sqlite:///{tmp_path / 'vision.sqlite'}"); await db.connect(); manager = make_stickers(db)
    assert "cute cat" in (await manager.semantic_for_unique_id("uk"))
    await db.close()


async def test_semantic_retrieval_and_anti_repeat(tmp_path):
    db = await make_db(tmp_path); manager = make_stickers(db)
    data = [("laugh", "ul", "cat laughing uncontrollably", ["laughter", "mockery", "absurdity"]), ("palm", "up", "cat covers face", ["facepalm", "cringe", "disbelief"]), ("kiss", "uk", "cute cat sends kiss", ["affection", "kiss", "warmth"]), ("cross", "uc", "cat holds cross in horror", ["horror", "cursed", "what_the_fuck"])]
    ids = {}
    for file_id, unique, visual, meanings in data:
        await manager.register_incoming(sticker(file_id, unique, set_name=None)); row = await db.fetchone("SELECT id FROM stickers WHERE file_unique_id=?", (unique,)); ids[file_id] = row["id"]; await save(manager, row["id"], visual, meanings)
    initial = await manager.candidates("что за проклятая хуйня", chat_id=1)
    assert initial[0]["id"] == ids["cross"]
    assert (await manager.candidates("нежно ответить на поцелуй", chat_id=1))[0]["id"] == ids["kiss"]
    await manager.record_usage(ids["cross"], 1, "outgoing"); await manager.record_usage(ids["cross"], 1, "outgoing"); await manager.record_usage(ids["cross"], 1, "outgoing")
    repeated = await manager.candidates("что за проклятая хуйня", chat_id=1)
    cross_after = next(candidate for candidate in repeated if candidate["id"] == ids["cross"])
    assert cross_after["score"] < initial[0]["score"]
    assert await manager.resolve_intent(StickerIntent(meaning="kiss", emotion="affection"), 1) == ids["kiss"]
    await db.close()


def test_static_frame_extraction_is_one_png_and_is_bounded():
    image = Image.new("RGB", (10, 10), "red"); source = io.BytesIO(); image.save(source, "WEBP")
    frames = StickerFrameExtractor(max_frames=99).extract(source.getvalue(), "static")
    assert len(frames) == 1 and frames[0].mime_type == "image/png"


def test_animated_tgs_frame_extraction_is_bounded():
    import gzip, json
    payload = {"v": "5.5.7", "fr": 30, "ip": 0, "op": 2, "w": 64, "h": 64, "layers": [{"ty": 4, "ks": {"o": {"a": 0, "k": 100}, "r": {"a": 0, "k": 0}, "p": {"a": 0, "k": [32, 32, 0]}, "a": {"a": 0, "k": [0, 0, 0]}, "s": {"a": 0, "k": [100, 100, 100]}}, "shapes": [{"ty": "rc", "p": {"a": 0, "k": [0, 0]}, "s": {"a": 0, "k": [30, 30]}, "r": {"a": 0, "k": 0}}, {"ty": "fl", "c": {"a": 0, "k": [1, 0, 0, 1]}, "o": {"a": 0, "k": 100}}], "ip": 0, "op": 2, "st": 0, "bm": 0}]}
    frames = StickerFrameExtractor(max_frames=5).extract(gzip.compress(json.dumps(payload).encode()), "animated")
    assert 1 <= len(frames) <= 5 and all(frame.mime_type == "image/png" for frame in frames)


def test_video_sticker_frame_extraction_is_bounded():
    # ffmpeg is the explicitly supported local WEBM renderer.
    with tempfile.NamedTemporaryFile(suffix=".webm") as handle:
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=16x16:rate=5:duration=1", "-c:v", "libvpx-vp9", "-y", handle.name], check=True)
        frames = StickerFrameExtractor(max_frames=3).extract(open(handle.name, "rb").read(), "video")
    assert 1 <= len(frames) <= 3 and all(frame.mime_type == "image/png" for frame in frames)


async def test_analysis_job_retries_then_fails_without_crashing(tmp_path):
    class Bot:
        async def get_file(self, _): return SimpleNamespace(file_path="x", file_size=1)
        async def download_file(self, _, destination): destination.write(b"RIFFxxxxWEBPraw")
    class Provider:
        model = "fake"
        async def analyze_sticker(self, _): raise RuntimeError("broken media")
    class Extractor:
        def extract(self, *_): return [ImageContent(data=b"png", mime_type="image/png")]
    settings = SimpleNamespace(sticker_analysis_max_frames=5, media_cache_dir=str(tmp_path), max_animation_bytes=100, max_sticker_bytes=100, sticker_analysis_retry_limit=2, sticker_analysis_version="v1")
    db = await make_db(tmp_path); manager = make_stickers(db); await manager.register_incoming(sticker("x", "ux", set_name=None))
    worker = StickerAnalysisWorker(db, Bot(), manager, Provider(), settings, extractor=Extractor())
    await worker.process_one(); await worker.process_one()
    assert (await db.fetchone("SELECT status,attempts FROM sticker_analysis_jobs"))["status"] == "failed"
    await db.close()


async def test_invalid_sticker_payload_is_not_cached_or_retried_four_times(tmp_path):
    class Bot:
        def __init__(self): self.downloads = 0
        async def get_file(self, _): return SimpleNamespace(file_path="x", file_size=4)
        async def download_file(self, _, destination):
            self.downloads += 1; destination.write(b"nope")

    settings = SimpleNamespace(sticker_analysis_max_frames=1, media_cache_dir=str(tmp_path), max_animation_bytes=100, max_sticker_bytes=100, sticker_analysis_retry_limit=4, sticker_analysis_version="v1")
    db = await make_db(tmp_path); manager = make_stickers(db); await manager.register_incoming(sticker("x", "ux", set_name=None))
    bot = Bot(); worker = StickerAnalysisWorker(db, bot, manager, SimpleNamespace(), settings)
    await worker.process_one(); await worker.process_one()
    row = await db.fetchone("SELECT status,attempts,last_error FROM sticker_analysis_jobs")
    assert (row["status"], row["attempts"], bot.downloads) == ("failed", 1, 1)
    assert "unsupported media signature" in row["last_error"]
    assert (await db.fetchone("SELECT local_cache_path FROM stickers"))["local_cache_path"] is None
    await db.close()


async def test_analysis_queue_recovers_processing_job_after_restart(tmp_path):
    db = await make_db(tmp_path); manager = make_stickers(db); await manager.register_incoming(sticker("x", "ux", set_name=None))
    await db.execute("UPDATE sticker_analysis_jobs SET status='processing'")
    worker = StickerAnalysisWorker(db, SimpleNamespace(), manager, SimpleNamespace(), SimpleNamespace(media_cache_dir=str(tmp_path), sticker_analysis_max_frames=1))
    await worker.recover_after_restart()
    assert (await db.fetchone("SELECT status FROM sticker_analysis_jobs"))["status"] == "pending"
    await db.close()


async def test_incoming_sticker_semantic_text_and_direct_action_compatibility(tmp_path):
    db = await make_db(tmp_path); manager = make_stickers(db); await manager.register_incoming(sticker("p", "up", set_name=None))
    row = await db.fetchone("SELECT id FROM stickers WHERE file_unique_id='up'"); await save(manager, row["id"], "cat facepalm", ["facepalm", "cringe"])
    assert "Смысл" in (await manager.semantic_for_unique_id("up"))
    assert Action(type=ActionType.sticker, sticker_id=row["id"]).sticker_id == row["id"]
    await db.close()


async def test_end_to_end_fake_sticker_intent_is_resolved_and_sent(tmp_path):
    class Bot:
        def __init__(self): self.sent = []
        async def send_sticker(self, chat_id, file_id):
            self.sent.append((chat_id, file_id)); return SimpleNamespace(message_id=55)
    db = await make_db(tmp_path); manager = make_stickers(db); await manager.register_incoming(sticker("kiss-file", "kiss-u", set_name=None))
    row = await db.fetchone("SELECT id FROM stickers WHERE file_unique_id='kiss-u'"); await save(manager, row["id"], "cute cat sends kiss", ["affection", "kiss"])
    bot = Bot(); queue = ActionQueue(TelegramActionExecutor(bot, db, stickers=manager))
    await queue.enqueue_many(1, "generation", [Action(type=ActionType.sticker, sticker_intent=StickerIntent(meaning="kiss", emotion="affection"))])
    await asyncio.sleep(.02)
    assert bot.sent == [(1, "kiss-file")]
    assert (await db.fetchone("SELECT type FROM messages WHERE sender='assistant'"))["type"] == "sticker"
    await db.close()
