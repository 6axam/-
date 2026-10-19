import asyncio, logging
from pathlib import Path
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
from app.social.retrieval import SocialMemoryRetrieval
from app.self_life import SelfLifeManager
from app.voice import DisabledVoiceProvider, OpenRouterSeedAudioProvider
from app.emotions.engine import EmotionalEngine
from app.emotions.affective import AffectiveEngine
from app.episodic_memory import EpisodicMemoryManager
from app.mind import SharedStateManager
from app.events import EventJournal, EventProcessor
from app.telegram.mtproto.client import MTProtoSettings, MTProtoTransport
from app.telegram.mtproto.runtime import MTProtoActionExecutor, MTProtoInboundBridge, MTProtoStickerDocuments, peer_id_for_chat
from app.telegram.identity import TelegramIdentityRegistry


def make_provider(settings, db=None):
    cls = OpenRouterProvider if settings.llm_provider == "openrouter" else OpenAIProvider
    from app.observability import ObservabilityWriter
    return cls(settings.llm_api_key, settings.llm_model, settings.llm_base_url,
               temperature=settings.llm_temperature,
               supports_vision=settings.llm_supports_vision,
               supports_multiple_images=settings.llm_supports_multiple_images,
               observability=ObservabilityWriter(db, store_prompts=settings.observability_store_prompts) if db else None)

def make_embeddings(settings):
    return LocalSemanticEmbeddingProvider(settings.sticker_embedding_model, settings.sticker_embedding_device)

def make_voice_provider(settings):
    if not settings.voice_generation_enabled:
        return DisabledVoiceProvider()
    api_key = settings.voice_api_key or settings.llm_api_key
    return OpenRouterSeedAudioProvider(
        api_key,
        settings.voice_model,
        settings.voice_base_url,
        settings.anya_voice_reference,
        timeout_seconds=settings.voice_request_timeout_seconds,
    )

async def main():
    logging.basicConfig(level=logging.INFO)
    s = load_settings(); db = Database(s.database_url); await db.connect()
    use_mtproto = s.telegram_transport == "mtproto"
    mtproto = None
    if use_mtproto:
        mtproto = MTProtoTransport(MTProtoSettings())
        # This is intentionally checked before background workers start. A
        # typo cannot turn an allowlist test into a broader live account.
        mtproto.settings.require_live_runtime()
    lifecycle = ConversationLifecycleManager(db, s.conversation_cooling_minutes, s.conversation_ended_hours)
    stickers = StickerManager(db, make_embeddings(s), repeat_window_hours=s.sticker_repeat_window_hours, strong_window_minutes=s.sticker_repeat_strong_window_minutes, selection_strategy=s.sticker_selection_strategy)
    # MTProto is a standalone account transport. Do not instantiate a Bot API
    # client or require its credentials merely because both implementations
    # share the same Python conversation pipeline.
    bot = None
    media = None
    if not use_mtproto:
        assert s.telegram_bot_token  # guaranteed by Settings validation
        bot = Bot(s.telegram_bot_token)
        media = MediaManager(db, bot, s.media_cache_dir, s.max_image_bytes)
        media.cleanup_expired(s.media_cache_max_age_days)
    if s.image_generation_enabled and s.image_generation_api_key and s.image_generation_model and s.image_generation_base_url:
        image_cls = OpenRouterImageProvider if s.image_generation_provider == "openrouter" else OpenAIImageProvider
        image_provider = image_cls(s.image_generation_api_key, s.image_generation_model, s.image_generation_base_url)
    else:
        image_provider = DisabledImageGenerationProvider()
    weather = WeatherService(s.weather_latitude, s.weather_longitude, enabled=s.weather_enabled, ttl_minutes=s.weather_cache_minutes)
    voice_provider = make_voice_provider(s)
    logging.getLogger(__name__).info(
        "voice_runtime enabled=%s provider=%s available=%s tendency=%.2f reference_exists=%s",
        s.voice_generation_enabled, getattr(voice_provider, "name", "unknown"), voice_provider.enabled,
        s.voice_message_tendency, Path(s.anya_voice_reference).is_file(),
    )
    if use_mtproto:
        mtproto_stickers = MTProtoStickerDocuments(db)
        executor = MTProtoActionExecutor(mtproto, db, stickers=stickers, lifecycle=lifecycle,
            image_provider=image_provider, image_prompts=ImagePromptBuilder(db, s.timezone, s.anya_reference_image, s.image_prompt_debug, weather=weather),
            image_daily_limit=s.image_generation_daily_limit, image_cooldown_hours=s.image_generation_cooldown_hours,
            voice_provider=voice_provider, sticker_documents=mtproto_stickers)
    else:
        executor = TelegramActionExecutor(bot, db, stickers=stickers, lifecycle=lifecycle, image_provider=image_provider, image_prompts=ImagePromptBuilder(db, s.timezone, s.anya_reference_image, s.image_prompt_debug, weather=weather), image_daily_limit=s.image_generation_daily_limit, image_cooldown_hours=s.image_generation_cooldown_hours, voice_provider=voice_provider)
    queue = ActionQueue(executor, transport="mtproto" if use_mtproto else "bot_api")
    recovery = await db.reconcile_action_dispatches(transport=queue.transport)
    if recovery["unknown"] or recovery["reclaimable_claims"]:
        logging.getLogger(__name__).warning(
            "action_dispatch_recovery transport=%s unknown=%s reclaimable_claims=%s",
            queue.transport, recovery["unknown"], recovery["reclaimable_claims"],
        )
    provider = make_provider(s, db)
    from app.character.manager import PersonalityManager
    personality, emotional_state = PersonalityManager(db), None
    emotion_engine = EmotionalEngine(db, s.timezone)
    affective_engine = AffectiveEngine(db, s.timezone)
    shared_state = SharedStateManager(db, enabled=s.anya_2_shared_state_enabled)
    await shared_state.bootstrap()
    event_processor = EventProcessor(EventJournal(db))
    from app.emotions.relationship import RelationshipBondManager
    relationship_manager = RelationshipBondManager(db)
    episodic_memory = EpisodicMemoryManager(db)
    memory_manager = MemoryManager(db)
    memory_retrieval = MemoryRetrieval(memory_manager)
    social_memory_retrieval = SocialMemoryRetrieval(db)
    self_life = SelfLifeManager(db, probability=s.self_life_continuation_probability)
    transport_name = "mtproto" if use_mtproto else "bot_api"
    scheduler = ResponseScheduler(db, ResponseTimingEngine(
        s.college_normal_delay_multiplier, s.college_active_delay_cap_seconds,
        s.active_reply_min_seconds, s.active_reply_max_seconds,
        s.active_busy_reply_max_seconds, s.active_away_reply_max_seconds,
    ), active_conversation_window_seconds=s.active_conversation_window_seconds, transport=transport_name)
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
                             reaction_tendency=s.reaction_tendency, voice_message_tendency=s.voice_message_tendency,
                             voice_message_available=voice_provider.enabled, emotion_engine=emotion_engine, episodic_memory=episodic_memory,
                             episodic_memory_token_budget=s.episodic_memory_context_token_budget,
                             episodic_memory_max_items=s.episodic_memory_max_context_items,
                             shared_state=shared_state,
                             social_memory_retrieval=social_memory_retrieval)
    context.affective_engine = affective_engine
    context.relationship_manager = relationship_manager
    splitter = MessageSplitter(enabled=s.message_split_enabled, target_chars=s.message_split_target_chars, min_chars=s.message_split_min_chars, max_parts=s.message_split_max_parts)
    presence = DailyPresenceManager(
        db, s.timezone, s.sleep_start_hour, s.wake_hour,
        s.college_start_hour, s.college_end_hour, s.college_weekdays,
    )
    manager = ConversationManager(provider, context, queue, personality, emotional_state, scheduler, splitter, lifecycle,
                                  media=media, presence=presence, memory_extractor=MemoryExtractor(), memory_manager=memory_manager,
                                  self_life=self_life, timezone_name=s.timezone, bedtime_ritual_enabled=s.bedtime_ritual_enabled,
                                  bedtime_window_minutes=s.bedtime_window_minutes, emotion_engine=emotion_engine, episodic_memory=episodic_memory,
                                  shared_state=shared_state, event_processor=event_processor,
                                  event_source="mtproto" if use_mtproto else "telegram_bot_api")
    manager.affective_engine = affective_engine
    read_scheduler = ReadScheduler(db, presence, ReadTimingEngine(s), transport=transport_name)

    async def on_delivery_status(chat_id: int, turn_id: int | None, status: str):
        if use_mtproto:
            await db.transition_telegram_turn(
                local_chat_id=chat_id, turn_id=turn_id, status=status,
                source="conversation_decision",
            )

    manager.delivery_status_hook = on_delivery_status if use_mtproto else None

    async def on_mtproto_message_read(update):
        if update.inbox:
            await db.confirm_mtproto_inbox_watermark(
                account_id=update.account_id, peer_id=update.peer_id,
                max_message_id=update.max_message_id, source="mtproto_message_read_inbox",
            )
        else:
            await db.record_mtproto_outbox_read_watermark(
                account_id=update.account_id, peer_id=update.peer_id,
                max_message_id=update.max_message_id, source="mtproto_message_read_outbox",
            )

    async def on_messages_read(record):
        """Read one durable batch only after its transport state is truthful."""
        if not await read_scheduler.is_current(record):
            return True
        chat_id, boundary = record["chat_id"], record["boundary_message_id"]
        rows = await db.read_batch_for_boundary(chat_id, boundary)
        if not rows:
            return True
        message_ids = [row["telegram_message_id"] for row in rows]
        buffered_content = buffer.content_for_messages(chat_id, message_ids)
        if not await read_scheduler.is_current(record):
            return True
        parts = []
        sticker_semantics = {}
        for row in rows:
            if row["type"] == "sticker":
                semantic = await stickers.semantic_for_file_id(row["sticker_file_id"])
                sticker_semantics[row["telegram_message_id"]] = semantic
                parts.append(semantic or "Собеседник отправил стикер.")
            elif row["type"] == "photo":
                parts.append((row["text"] or "[Собеседник отправил изображение]").strip())
            else:
                # Older MTProto rows may predate durable media markers. Keep
                # their meaning on replay instead of collapsing them to an
                # empty turn after a restart.
                parts.append(row["text"] or {
                    "voice": "[голосовое сообщение]", "audio": "[аудио]",
                    "video": "[видео]", "animation": "[GIF-анимация]",
                    "document": "[документ]",
                }.get(row["type"], ""))
        merged = "\n".join(part for part in parts if part) or "[сообщение без текста]"
        user_id = rows[-1]["user_id"]
        # A newer arrival during the tiny claim→dispatch race must receive its
        # own read event rather than triggering a reply to the old batch.
        if await db.has_unread_user_message_after(chat_id, boundary):
            logging.getLogger(__name__).info("read_batch_superseded chat_id=%s boundary_message_id=%s", chat_id, boundary)
            return True
        turn_id = await db.ensure_turn_for_batch(
            user_id=user_id, chat_id=chat_id, merged_text=merged, telegram_message_ids=message_ids,
        )
        images = list(await media.inputs_for_messages(chat_id, message_ids)) if media and provider.supports_vision else []
        if provider.supports_vision:
            for row in rows:
                message_id = row["telegram_message_id"]
                if row["type"] == "sticker" and not sticker_semantics.get(message_id):
                    images.extend(buffered_content.get(message_id, []))
        if not provider.supports_vision and any(row["type"] == "photo" for row in rows):
            logging.getLogger(__name__).info("image_vision_skipped reason=provider_disabled chat_id=%s", chat_id)
        if mtproto and chat_id < 0:
            await db.transition_telegram_messages(
                local_chat_id=chat_id, message_ids=message_ids,
                status="read_ack_requested", source="mtproto_read_request",
            )
            try:
                acknowledgement = await mtproto.mark_read(peer_id_for_chat(chat_id), max(message_ids))
            except Exception:
                logging.getLogger(__name__).warning("mtproto_read_receipt_failed chat_id=%s", chat_id, exc_info=True)
                return False
            if not acknowledgement.confirmed:
                await db.mark_mtproto_read_confirmation_pending(
                    local_chat_id=chat_id, message_ids=message_ids,
                    read_watermark=acknowledgement.server_watermark,
                    source="mtproto_read_confirmation_pending",
                )
                # A MessageRead update may have raced the RPC/watermark query.
                # The DB is the durable authority for that event, so do not
                # schedule another read or block the turn once it is present.
                if not await db.mtproto_inbox_read_confirmed(local_chat_id=chat_id, message_ids=message_ids):
                    logging.getLogger(__name__).info(
                        "mtproto_read_receipt_waiting_confirmation chat_id=%s boundary_message_id=%s server_watermark=%s",
                        chat_id, boundary, acknowledgement.server_watermark,
                    )
                    return False
            await db.confirm_mtproto_inbox_watermark(
                account_id=await mtproto.account_id(), peer_id=peer_id_for_chat(chat_id),
                max_message_id=max(message_ids), source="mtproto_server_watermark",
            )
            # `manager.interrupt()` cancels this durable read as soon as a new
            # inbound message is stored. Acknowledging the old batch may take
            # a network round-trip, so fence again immediately before an LLM
            # decision. Otherwise an obsolete response could escape first.
            if not await read_scheduler.is_current(record):
                logging.getLogger(__name__).info("mtproto_read_batch_cancelled_before_decision chat_id=%s boundary_message_id=%s", chat_id, boundary)
                return True
            await db.transition_telegram_messages(
                local_chat_id=chat_id, message_ids=message_ids,
                status="decision_pending", source="conversation_start",
            )
            await db.mark_messages_read(chat_id, message_ids)
        await manager.handle_turn(user_id, chat_id, merged, turn_id, message_ids[-1], images)
        await db.mark_messages_read(chat_id, message_ids)
        buffer.clear_content(chat_id, message_ids)
        return True

    read_scheduler.bind(on_messages_read)

    async def flush(chat_id, messages):
        if not messages: return
        user_id = messages[-1].from_user.id
        force_now = any(ConversationManager.TEST_WAKE_PHRASE in (message.text or message.caption or "").lower() for message in messages)
        await read_scheduler.schedule(user_id, chat_id, max(message.message_id for message in messages), force_now=force_now)
        if use_mtproto and chat_id < 0:
            await db.transition_telegram_messages(
                local_chat_id=chat_id,
                message_ids=[message.message_id for message in messages],
                status="scheduled_read", source="read_scheduler",
            )
    buffer = IncomingBuffer(s.debounce_seconds,flush)
    mtproto_bridge = MTProtoInboundBridge(
        db, buffer, manager, lifecycle, stickers, mtproto_stickers,
        identity_registry=TelegramIdentityRegistry(db),
    ) if mtproto else None
    initiative = InitiativeScheduler(db, manager, lifecycle, scheduler, InitiativeContextBuilder(context, lifecycle, presence), s, buffer, presence)
    manager.initiative_scheduler = initiative
    worker = StickerAnalysisWorker(db, bot, stickers, provider, s) if bot else None
    daily_life = DailyLifeScheduler(db, provider, presence, s)
    photo_worker = MediaDescriptionWorker(db, provider)
    dp = None
    if not use_mtproto:
        assert bot is not None and media is not None and worker is not None and s.owner_telegram_id is not None
        dp = Dispatcher(); dp.include_router(make_router(db, buffer, manager, s.owner_telegram_id, stickers=stickers, lifecycle=lifecycle, media=media, analysis_worker=worker if s.sticker_analysis_enabled and provider.supports_vision else None, current_analysis_timeout=s.sticker_current_analysis_timeout_seconds))
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(scheduler.run())
            tg.create_task(read_scheduler.run())
            if not use_mtproto:
                tg.create_task(initiative.run())
                if s.daily_life_enabled: tg.create_task(daily_life.run())
            if not use_mtproto and s.sticker_analysis_enabled and provider.supports_vision:
                for _ in range(min(4, max(1, s.sticker_analysis_workers))): tg.create_task(worker.run())
            if not use_mtproto and provider.supports_vision:
                tg.create_task(photo_worker.run())
            if mtproto:
                tg.create_task(mtproto.run_private_inbound(mtproto_bridge.receive, on_mtproto_message_read))
            else:
                tg.create_task(dp.start_polling(bot))
    finally:
        if mtproto:
            await mtproto.close()
        await db.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except RuntimeError as exc:
        if str(exc).startswith("Invalid configuration."):
            raise SystemExit(str(exc)) from None
        raise
