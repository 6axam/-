import asyncio, logging
from aiogram import Bot, Dispatcher
from app.config import load_settings
from app.database.db import Database
from app.llm.openai_provider import OpenAIProvider
from app.llm.openrouter_provider import OpenRouterProvider
from app.conversation.context import ContextBuilder
from app.conversation.manager import ConversationManager
from app.conversation.response_scheduler import ResponseScheduler
from app.conversation.read_scheduler import ReadScheduler, ReadTimingEngine
from app.conversation.response_timing import ResponseTimingEngine
from app.conversation.message_splitter import MessageSplitter
from app.conversation.lifecycle import ConversationLifecycleManager
from app.actions.queue import ActionQueue
from app.telegram.executor import TelegramActionExecutor
from app.telegram.buffer import IncomingBuffer
from app.telegram.handlers import make_router
from app.initiative.context import InitiativeContextBuilder
from app.initiative.scheduler import InitiativeScheduler
from app.stickers.manager import StickerManager
from app.stickers.analyzer import StickerAnalysisWorker
from app.media.manager import MediaManager
from app.media.analyzer import MediaDescriptionWorker
from app.stickers.retriever import LocalSemanticEmbeddingProvider
from app.presence import DailyPresenceManager
from app.daily_life import DailyLifeScheduler
from app.weather import WeatherService
from app.images import DisabledImageGenerationProvider, OpenAIImageProvider, OpenRouterImageProvider, ImagePromptBuilder
from app.memory.extractor import MemoryExtractor
from app.memory.manager import MemoryManager
from app.memory.retrieval import MemoryRetrieval
from app.self_life import SelfLifeManager


def make_provider(settings):
    cls = OpenRouterProvider if settings.llm_provider == "openrouter" else OpenAIProvider
    return cls(settings.llm_api_key, settings.llm_model, settings.llm_base_url,
               temperature=settings.llm_temperature,
               supports_vision=settings.llm_supports_vision,
               supports_multiple_images=settings.llm_supports_multiple_images)

def make_embeddings(settings):
    return LocalSemanticEmbeddingProvider(settings.sticker_embedding_model, settings.sticker_embedding_device)

async def main():
    logging.basicConfig(level=logging.INFO)
    s = load_settings(); db = Database(s.database_url); await db.connect()
    lifecycle = ConversationLifecycleManager(db, s.conversation_cooling_minutes, s.conversation_ended_hours)
    stickers = StickerManager(db, make_embeddings(s), repeat_window_hours=s.sticker_repeat_window_hours, strong_window_minutes=s.sticker_repeat_strong_window_minutes, selection_strategy=s.sticker_selection_strategy)
    bot = Bot(s.telegram_bot_token); media = MediaManager(db, bot, s.media_cache_dir, s.max_image_bytes)
    media.cleanup_expired(s.media_cache_max_age_days)
    if s.image_generation_enabled and s.image_generation_api_key and s.image_generation_model and s.image_generation_base_url:
        image_cls = OpenRouterImageProvider if s.image_generation_provider == "openrouter" else OpenAIImageProvider
        image_provider = image_cls(s.image_generation_api_key, s.image_generation_model, s.image_generation_base_url)
    else:
        image_provider = DisabledImageGenerationProvider()
    weather = WeatherService(s.weather_latitude, s.weather_longitude, enabled=s.weather_enabled, ttl_minutes=s.weather_cache_minutes)
    executor = TelegramActionExecutor(bot, db, stickers=stickers, lifecycle=lifecycle, image_provider=image_provider, image_prompts=ImagePromptBuilder(db, s.timezone, s.anya_reference_image, s.image_prompt_debug, weather=weather), image_daily_limit=s.image_generation_daily_limit, image_cooldown_hours=s.image_generation_cooldown_hours); queue = ActionQueue(executor)
    provider = make_provider(s)
    from app.character.manager import EmotionalStateManager, PersonalityManager
    personality, emotional_state = PersonalityManager(db), EmotionalStateManager(db)
    memory_manager = MemoryManager(db)
    memory_retrieval = MemoryRetrieval(memory_manager)
    self_life = SelfLifeManager(db, probability=s.self_life_continuation_probability)
    scheduler = ResponseScheduler(db, ResponseTimingEngine(
        s.college_normal_delay_multiplier, s.college_active_delay_cap_seconds,
        s.active_reply_min_seconds, s.active_reply_max_seconds,
        s.active_busy_reply_max_seconds, s.active_away_reply_max_seconds,
    ), active_conversation_window_seconds=s.active_conversation_window_seconds)
    context = ContextBuilder(db, personality, emotional_state, stickers,
                             recent_media_hours=s.recent_media_context_hours,
                             recent_max_messages=s.context_recent_max_messages,
                             recent_token_budget=s.context_recent_token_budget,
                             target_input_tokens=s.context_target_input_tokens,
                             memory_retrieval=memory_retrieval,
                             memory_token_budget=s.memory_context_token_budget,
                             memory_max_items=s.memory_context_max_items,
                             self_life=self_life,
                             self_life_token_budget=s.self_life_context_token_budget,
                             self_life_max_items=s.self_life_context_max_items,
                             timezone_name=s.timezone, sticker_tendency=s.sticker_tendency,
                             reaction_tendency=s.reaction_tendency)
    splitter = MessageSplitter(enabled=s.message_split_enabled, target_chars=s.message_split_target_chars, min_chars=s.message_split_min_chars, max_parts=s.message_split_max_parts)
    presence = DailyPresenceManager(
        db, s.timezone, s.sleep_start_hour, s.wake_hour,
        s.college_start_hour, s.college_end_hour, s.college_weekdays,
    )
    manager = ConversationManager(provider, context, queue, personality, emotional_state, scheduler, splitter, lifecycle,
                                  media=media, presence=presence, memory_extractor=MemoryExtractor(), memory_manager=memory_manager,
                                  self_life=self_life, timezone_name=s.timezone)
    read_scheduler = ReadScheduler(db, presence, ReadTimingEngine(s))

    async def on_messages_read(record):
        """Internal-read hook; a future MTProto adapter can send receipts here."""
        if not await read_scheduler.is_current(record):
            return
        chat_id, boundary = record["chat_id"], record["boundary_message_id"]
        rows = await db.unread_user_messages_up_to(chat_id, boundary)
        if not rows:
            return
        message_ids = [row["telegram_message_id"] for row in rows]
        # Store the boundary before conversation work. Later arrivals remain
        # unread and will get their own job instead of leaking into this turn.
        await db.mark_messages_read(chat_id, message_ids)
        if not await read_scheduler.is_current(record):
            return
        parts = []
        for row in rows:
            if row["type"] == "sticker":
                semantic = await stickers.semantic_for_file_id(row["sticker_file_id"])
                parts.append(semantic or "Максим отправил стикер.")
            elif row["type"] == "photo":
                parts.append((row["text"] or "[Максим отправил изображение]").strip())
            else:
                parts.append(row["text"] or "")
        merged = "\n".join(part for part in parts if part) or "[сообщение без текста]"
        user_id = rows[-1]["user_id"]
        # A newer arrival during the tiny claim→dispatch race must receive its
        # own read event rather than triggering a reply to the old batch.
        if await db.has_unread_user_message_after(chat_id, boundary):
            logging.getLogger(__name__).info("read_batch_superseded chat_id=%s boundary_message_id=%s", chat_id, boundary)
            return
        turn_id = await db.create_turn(
            user_id=user_id, chat_id=chat_id, merged_text=merged, telegram_message_ids=message_ids,
        )
        images = await media.inputs_for_messages(chat_id, message_ids) if provider.supports_vision else []
        if not provider.supports_vision and any(row["type"] == "photo" for row in rows):
            logging.getLogger(__name__).info("image_vision_skipped reason=provider_disabled chat_id=%s", chat_id)
        await manager.handle_turn(user_id, chat_id, merged, turn_id, message_ids[-1], images)

    read_scheduler.bind(on_messages_read)

    async def flush(chat_id, messages):
        if not messages: return
        user_id = messages[-1].from_user.id
        await read_scheduler.schedule(user_id, chat_id, max(message.message_id for message in messages))
    buffer = IncomingBuffer(s.debounce_seconds,flush)
    initiative = InitiativeScheduler(db, manager, lifecycle, scheduler, InitiativeContextBuilder(context, lifecycle, presence), s, buffer, presence)
    manager.initiative_scheduler = initiative
    worker = StickerAnalysisWorker(db, bot, stickers, provider, s)
    daily_life = DailyLifeScheduler(db, provider, presence, s)
    photo_worker = MediaDescriptionWorker(db, provider)
    dp = Dispatcher(); dp.include_router(make_router(db, buffer, manager, s.owner_telegram_id, stickers=stickers, lifecycle=lifecycle, media=media, analysis_worker=worker if s.sticker_analysis_enabled and provider.supports_vision else None, current_analysis_timeout=s.sticker_current_analysis_timeout_seconds))
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(scheduler.run())
            tg.create_task(read_scheduler.run())
            tg.create_task(initiative.run())
            if s.daily_life_enabled: tg.create_task(daily_life.run())
            if s.sticker_analysis_enabled and provider.supports_vision:
                for _ in range(min(4, max(1, s.sticker_analysis_workers))): tg.create_task(worker.run())
            if provider.supports_vision:
                tg.create_task(photo_worker.run())
            tg.create_task(dp.start_polling(bot))
    finally: await db.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except RuntimeError as exc:
        if str(exc).startswith("Invalid configuration."):
            raise SystemExit(str(exc)) from None
        raise
