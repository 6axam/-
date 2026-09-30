import asyncio
import logging
from pathlib import Path

from app.llm.schemas import ImageContent

log = logging.getLogger(__name__)


class MediaDescriptionWorker:
    """Small persistent photo-description queue; descriptions, not binaries, enter history."""
    def __init__(self, db, provider, retry_limit: int = 2):
        self.db, self.provider, self.retry_limit, self._stop = db, provider, retry_limit, asyncio.Event()

    async def recover_after_restart(self):
        await self.db.execute("UPDATE media_analysis_jobs SET status='pending',updated_at=CURRENT_TIMESTAMP WHERE status='processing'")

    async def process_one(self) -> bool:
        job = await self.db.fetchone("SELECT j.*,m.local_cache_path,m.mime_type FROM media_analysis_jobs j JOIN media m ON m.id=j.media_id WHERE j.status='pending' ORDER BY j.id LIMIT 1")
        if not job: return False
        claimed = await self.db.execute("UPDATE media_analysis_jobs SET status='processing',attempts=attempts+1,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'", (job["id"],))
        if not claimed.rowcount: return True
        try:
            path = Path(job["local_cache_path"] or "")
            if not path.is_file(): raise ValueError("photo cache unavailable")
            description = await self.provider.describe_image(ImageContent(data=path.read_bytes(), mime_type=job["mime_type"] or "image/jpeg", source="telegram_photo"))
            await self.db.execute("INSERT INTO media_descriptions(media_id,description,model) VALUES(?,?,?) ON CONFLICT(media_id) DO UPDATE SET description=excluded.description,model=excluded.model,created_at=CURRENT_TIMESTAMP", (job["media_id"], description, self.provider.model))
            await self.db.execute("UPDATE media_analysis_jobs SET status='done',last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?", (job["id"],))
            log.info("photo_description_completed media_id=%s", job["media_id"])
        except Exception as exc:
            attempts = (await self.db.fetchone("SELECT attempts FROM media_analysis_jobs WHERE id=?", (job["id"],)))["attempts"]
            status = "failed" if attempts >= self.retry_limit else "pending"
            await self.db.execute("UPDATE media_analysis_jobs SET status=?,last_error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, str(exc)[:500], job["id"]))
            log.warning("photo_description_failed media_id=%s", job["media_id"], exc_info=True)
        return True

    async def run(self):
        await self.recover_after_restart()
        while not self._stop.is_set():
            if not await self.process_one():
                try: await asyncio.wait_for(self._stop.wait(), timeout=1)
                except asyncio.TimeoutError: pass

    def stop(self): self._stop.set()
