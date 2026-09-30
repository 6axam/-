import io
import logging
import time
from pathlib import Path

from app.llm.schemas import ImageContent

log = logging.getLogger(__name__)


class MediaManager:
    """Downloads bounded Telegram media and records metadata, never blobs in SQLite."""
    def __init__(self, db, bot, cache_dir: str, max_image_bytes: int):
        self.db, self.bot, self.cache_dir, self.max_image_bytes = db, bot, Path(cache_dir), max_image_bytes
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def cleanup_expired(self, max_age_days: int) -> int:
        """Best-effort cache eviction; DB keeps recoverable Telegram file IDs."""
        cutoff, removed = time.time() - max(0, max_age_days) * 86400, 0
        for path in self.cache_dir.rglob("*"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink(); removed += 1
            except OSError:
                log.warning("media_cache_cleanup_failed path=%s", path)
        if removed: log.info("media_cache_cleaned removed=%s", removed)
        return removed

    async def _download(self, file_id: str, max_bytes: int) -> bytes | None:
        try:
            telegram_file = await self.bot.get_file(file_id)
            declared = getattr(telegram_file, "file_size", None)
            if declared and declared > max_bytes:
                log.warning("media_download_rejected file_id=%s reason=size", file_id)
                return None
            stream = io.BytesIO()
            await self.bot.download_file(telegram_file.file_path, destination=stream)
            data = stream.getvalue()
            if len(data) > max_bytes:
                log.warning("media_download_rejected file_id=%s reason=actual_size", file_id)
                return None
            return data
        except Exception:
            log.exception("media_download_failed file_id=%s", file_id)
            return None

    async def ingest_photo(self, *, chat_id: int, message_id: int, photo) -> ImageContent | None:
        data = await self._download(photo.file_id, self.max_image_bytes)
        path = None
        if data:
            path = self.cache_dir / f"photo-{photo.file_unique_id}.jpg"
            path.write_bytes(data)
        await self.db.record_media(chat_id=chat_id, telegram_message_id=message_id, file_id=photo.file_id,
                                   file_unique_id=getattr(photo, "file_unique_id", None), media_type="photo",
                                   mime_type="image/jpeg", local_cache_path=str(path) if path else None,
                                   byte_size=len(data) if data else None)
        row = await self.db.fetchone("SELECT id FROM media WHERE chat_id=? AND telegram_file_id=?", (chat_id, photo.file_id))
        if row and data:
            await self.db.queue_media_description(row["id"])
        log.info("media_received kind=photo chat_id=%s cached=%s", chat_id, bool(data))
        return ImageContent(data=data, mime_type="image/jpeg", source="telegram_photo") if data else None

    async def inputs_for_messages(self, chat_id: int, message_ids: list[int]) -> list[ImageContent]:
        if not message_ids:
            return []
        marks = ",".join("?" for _ in message_ids)
        rows = await self.db.fetchall(
            f"SELECT m.local_cache_path,m.mime_type FROM media m JOIN message_media mm ON mm.media_id=m.id "
            f"WHERE mm.chat_id=? AND mm.telegram_message_id IN ({marks}) AND m.media_type='photo' ORDER BY m.id",
            (chat_id, *message_ids),
        )
        result = []
        for row in rows:
            path = Path(row["local_cache_path"] or "")
            try:
                if path.is_file():
                    data = path.read_bytes()
                    if data and len(data) <= self.max_image_bytes:
                        result.append(ImageContent(data=data, mime_type=row["mime_type"] or "image/jpeg", source="telegram_photo"))
            except OSError:
                log.warning("media_cache_read_failed path=%s", path)
        return result
