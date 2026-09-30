from aiogram import Router, F
from aiogram.types import Message, MessageReactionUpdated, Update
from app.telegram.security import is_owner

def make_router(db, buffer, manager, owner_telegram_id: int, stickers=None, lifecycle=None, media=None, analysis_worker=None, current_analysis_timeout: float = 0):
    router = Router()
    @router.message_reaction()
    async def reaction(event: MessageReactionUpdated, event_update: Update):
        # Reactions deliberately bypass debounce, interruption and LLM generation.
        # They are nonverbal facts that can colour a later answer.
        if not event.user or not is_owner(event.user.id, owner_telegram_id):
            return
        if not await db.claim_update(event_update.update_id):
            return
        await db.ensure_user(event.user.id, event.user.username)
        for value in event.old_reaction:
            emoji = getattr(value, "emoji", None)
            if emoji:
                await db.record_reaction(chat_id=event.chat.id, telegram_message_id=event.message_id, actor="user", actor_user_id=event.user.id, emoji=emoji, active=False)
        for value in event.new_reaction:
            emoji = getattr(value, "emoji", None)
            if emoji:
                await db.record_reaction(chat_id=event.chat.id, telegram_message_id=event.message_id, actor="user", actor_user_id=event.user.id, emoji=emoji, active=True)
                await manager.observe_reaction(event.chat.id, emoji)
    @router.message(F.text)
    async def text(message: Message, event_update: Update):
        if not is_owner(message.from_user.id if message.from_user else None, owner_telegram_id):
            return
        if not await db.claim_update(event_update.update_id):
            return
        await db.ensure_user(message.from_user.id, message.from_user.username)
        inserted = await db.record_message(chat_id=message.chat.id, telegram_message_id=message.message_id, user_id=message.from_user.id, sender="user", kind="text", text=message.text, reply_to=message.reply_to_message.message_id if message.reply_to_message else None)
        if inserted is None:
            return
        manager.interrupt(message.chat.id)
        if lifecycle: await lifecycle.on_user_message(message.chat.id)
        await buffer.add(message.chat.id,message)
    @router.message(F.photo)
    async def photo(message: Message, event_update: Update):
        if not is_owner(message.from_user.id if message.from_user else None, owner_telegram_id): return
        if not await db.claim_update(event_update.update_id): return
        await db.ensure_user(message.from_user.id, message.from_user.username)
        caption = message.caption or ""
        inserted = await db.record_message(chat_id=message.chat.id, telegram_message_id=message.message_id, user_id=message.from_user.id, sender="user", kind="photo", text=caption or "[photo]", reply_to=message.reply_to_message.message_id if message.reply_to_message else None)
        if inserted is None: return
        manager.interrupt(message.chat.id)
        if lifecycle: await lifecycle.on_user_message(message.chat.id)
        if media:
            await media.ingest_photo(chat_id=message.chat.id, message_id=message.message_id, photo=message.photo[-1])
        await buffer.add(message.chat.id, message)
    @router.message(F.sticker)
    async def sticker(message: Message, event_update: Update):
        if not is_owner(message.from_user.id if message.from_user else None, owner_telegram_id):
            return
        if not await db.claim_update(event_update.update_id):
            return
        s = message.sticker
        await db.ensure_user(message.from_user.id, message.from_user.username)
        inserted = await db.record_message(chat_id=message.chat.id, telegram_message_id=message.message_id, user_id=message.from_user.id, sender="user", kind="sticker", sticker_file_id=s.file_id, reply_to=message.reply_to_message.message_id if message.reply_to_message else None)
        if inserted is None:
            return
        manager.interrupt(message.chat.id)
        if lifecycle: await lifecycle.on_user_message(message.chat.id)
        sticker_id = None
        if stickers:
            sticker_id = await stickers.register_incoming(s)
        if stickers and s.set_name:
            try: await stickers.import_set(await message.bot.get_sticker_set(s.set_name), current_file_unique_id=s.file_unique_id)
            except Exception: pass
        if stickers: await stickers.seen(s, message.chat.id)
        if analysis_worker and sticker_id and current_analysis_timeout > 0:
            fallback_frames = []
            import asyncio
            frame_task = asyncio.create_task(analysis_worker.prepare_frames(sticker_id))
            try:
                # Shield the durable job: timeout only releases the Telegram
                # update, never strands the row in `processing`.
                task = asyncio.create_task(analysis_worker.process_sticker(sticker_id))
                await asyncio.wait_for(asyncio.shield(task), timeout=current_analysis_timeout)
                if not await stickers.semantic_for_unique_id(s.file_unique_id):
                    fallback_frames = await asyncio.wait_for(asyncio.shield(frame_task), timeout=current_analysis_timeout)
            except asyncio.TimeoutError:
                import logging
                logging.getLogger(__name__).info("sticker_current_analysis_deferred sticker_id=%s", sticker_id)
                try:
                    # The analyzer shares this task, so this never re-downloads
                    # the sticker. A short bounded wait avoids a blind answer.
                    fallback_frames = await asyncio.wait_for(asyncio.shield(frame_task), timeout=current_analysis_timeout)
                except Exception:
                    logging.getLogger(__name__).warning("sticker_fallback_frames_unavailable sticker_id=%s", sticker_id, exc_info=True)
            except Exception:
                import logging
                logging.getLogger(__name__).warning("sticker_current_analysis_failed sticker_id=%s", sticker_id, exc_info=True)
            await buffer.add(message.chat.id, message, user_content=fallback_frames)
            return
        await buffer.add(message.chat.id,message)
    return router
