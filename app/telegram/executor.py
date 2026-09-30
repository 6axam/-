import asyncio
import time
from aiogram.enums import ChatAction
from aiogram.types import BufferedInputFile, ReactionTypeEmoji
from app.actions.models import ActionType, Duration
from app.actions.timing import TimingEngine
from app.telegram.text import strip_emoji

class TelegramActionExecutor:
    typing_refresh_seconds = 4.0

    def __init__(self, bot, db, stickers=None, lifecycle=None, image_provider=None, image_prompts=None, image_daily_limit=2, image_cooldown_hours=12): self.bot,self.db,self.stickers,self.lifecycle,self.image_provider,self.image_prompts,self.image_daily_limit,self.image_cooldown_hours,self.timing = bot,db,stickers,lifecycle,image_provider,image_prompts,image_daily_limit,image_cooldown_hours,TimingEngine()

    async def start_generation_typing(self, chat_id: int):
        """Show typing while an LLM request is in flight, refreshing it safely."""
        await self.bot.send_chat_action(chat_id, ChatAction.TYPING)

        async def refresh():
            while True:
                await asyncio.sleep(self.typing_refresh_seconds)
                try:
                    await self.bot.send_chat_action(chat_id, ChatAction.TYPING)
                except Exception:
                    # A failed nonessential chat-action must never cancel LLM work.
                    import logging
                    logging.getLogger(__name__).debug("generation_typing_refresh_failed chat_id=%s", chat_id, exc_info=True)

        return asyncio.create_task(refresh())

    async def stop_generation_typing(self, session) -> None:
        session.cancel()
        try:
            await session
        except asyncio.CancelledError:
            pass

    async def execute(self, item):
        a = item.action
        if a.type == ActionType.pause:
            await asyncio.sleep(self.timing.between_messages(a.text or "") if a.automatic else self.timing.pause(a.duration or Duration.short))
        elif a.type == ActionType.text and a.text:
            text = strip_emoji(a.text)
            if not text.strip():
                return
            await self.bot.send_chat_action(item.chat_id, ChatAction.TYPING); await asyncio.sleep(self.timing.typing_seconds(text))
            reply = {"reply_parameters": {"message_id": a.reply_to_message_id}} if a.reply_to_message_id else {}
            sent = await self.bot.send_message(item.chat_id, text, **reply)
            await self.db.record_message(chat_id=item.chat_id, telegram_message_id=sent.message_id, sender="assistant", kind="text", text=text)
            if self.lifecycle: await self.lifecycle.on_bot_message(item.chat_id)
        elif a.type == ActionType.sticker and self.stickers:
            sticker_id = a.sticker_id or (await self.stickers.resolve_intent(a.sticker_intent, item.chat_id) if a.sticker_intent else None)
            if not sticker_id:
                return
            file_id = await self.stickers.file_id(sticker_id)
            if file_id:
                sent = await self.bot.send_sticker(item.chat_id, file_id)
                await self.db.record_message(chat_id=item.chat_id, telegram_message_id=sent.message_id, sender="assistant", kind="sticker", sticker_file_id=file_id)
                await self.stickers.db.execute("UPDATE stickers SET times_sent=times_sent+1 WHERE id=?", (sticker_id,))
                await self.stickers.record_usage(sticker_id, item.chat_id, "outgoing")
        elif a.type == ActionType.image and a.image_intent and self.image_provider:
            try:
                if not self.image_prompts or not await self.image_prompts.allowed(item.chat_id, self.image_daily_limit, self.image_cooldown_hours): return
                prompt, visual, references = await self.image_prompts.build(item.chat_id, a.image_intent)
                kind = a.image_intent.kind.value
                await self.db.execute("INSERT INTO image_generation_usage(chat_id,provider,prompt,status) VALUES(?,?,?,'processing')", (item.chat_id, self.image_provider.name, prompt))
                generated = await self._generate_image_with_progress(prompt, references, item.chat_id, kind)
                photo = BufferedInputFile(generated.data, filename="anya.jpg")
                sent = await self.bot.send_photo(item.chat_id, photo=photo, caption=a.image_intent.caption)
                await self.db.record_message(chat_id=item.chat_id, telegram_message_id=sent.message_id, sender="assistant", kind="photo", text=a.image_intent.caption or "[generated image]")
                await self.db.execute("UPDATE image_generation_usage SET status='sent',cost=? WHERE id=(SELECT max(id) FROM image_generation_usage WHERE chat_id=? AND status='processing')", (generated.cost,item.chat_id))
                await self.db.execute("INSERT INTO generated_images(chat_id,telegram_message_id,kind,scene,location,activity,clothing_context,provider,model,status) VALUES(?,?,?,?,?,?,?,?,?,?)", (item.chat_id,sent.message_id,kind,a.image_intent.scene,visual['location'],visual['activity'],visual['clothing'],self.image_provider.name,getattr(self.image_provider,'model',''),'sent'))
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("image_generation_failed chat_id=%s error=%s", item.chat_id, exc)
                await self.db.execute("UPDATE image_generation_usage SET status='failed' WHERE id=(SELECT max(id) FROM image_generation_usage WHERE chat_id=? AND status='processing')", (item.chat_id,))
        elif a.type == ActionType.reaction and a.emoji:
            if not a.target_message_id:
                import logging
                logging.getLogger(__name__).debug("Skipping reaction without target message id")
                return
            await self.bot.set_message_reaction(item.chat_id, a.target_message_id, reaction=[ReactionTypeEmoji(emoji=a.emoji)])
            await self.db.record_reaction(chat_id=item.chat_id, telegram_message_id=a.target_message_id, actor="assistant", emoji=a.emoji)

    async def _generate_image_with_progress(self, prompt, references, chat_id, kind):
        """Terminal-only progress indicator; it never blocks Telegram updates."""
        task = asyncio.create_task(self.image_provider.generate(prompt, references))
        started = time.monotonic()
        try:
            while not task.done():
                elapsed = int(time.monotonic() - started)
                print(f"\r🖼️  Генерация изображения ({kind})… {elapsed}с", end="", flush=True)
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=1)
                except asyncio.TimeoutError:
                    pass
            result = await task
            elapsed = time.monotonic() - started
            print(f"\r🖼️  Изображение готово ({kind}) за {elapsed:.1f}с{' ' * 24}")
            return result
        except BaseException:
            elapsed = time.monotonic() - started
            print(f"\r🖼️  Генерация изображения не завершилась ({elapsed:.1f}с){' ' * 16}")
            if not task.done(): task.cancel()
            raise
