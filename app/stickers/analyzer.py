import asyncio
import io
import logging
from pathlib import Path

from app.stickers.frames import StickerFrameExtractor, StickerMediaValidationError, detect_sticker_type

log = logging.getLogger(__name__)


class StickerAnalysisWorker:
    """One restart-safe worker: pack imports never block an ordinary Telegram update."""
    def __init__(self, db, bot, stickers, provider, settings, extractor=None):
        self.db, self.bot, self.stickers, self.provider, self.settings = db, bot, stickers, provider, settings
        self.extractor = extractor or StickerFrameExtractor(settings.sticker_analysis_max_frames)
        self._stop = asyncio.Event()
        self.cache_dir = Path(settings.media_cache_dir) / "stickers"; self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._frame_tasks: dict[int, asyncio.Task] = {}

    async def _download(self, file_id: str, maximum: int) -> bytes:
        telegram_file = await self.bot.get_file(file_id)
        if getattr(telegram_file, "file_size", 0) > maximum: raise ValueError("sticker exceeds configured size limit")
        stream = io.BytesIO(); await self.bot.download_file(telegram_file.file_path, destination=stream)
        data = stream.getvalue()
        if len(data) > maximum: raise ValueError("sticker exceeds configured size limit")
        return data

    @staticmethod
    def _suffix(sticker_type: str) -> str:
        return {"static": ".webp", "animated": ".tgs", "video": ".webm"}[sticker_type]

    async def _invalidate_cache(self, sticker_id: int, path: Path, *, reason: str):
        if path.is_file():
            path.unlink()
        await self.db.execute(
            "UPDATE stickers SET local_cache_path=NULL,cached_sticker_type=NULL,cache_byte_size=NULL,cache_validated_at=NULL WHERE id=?",
            (sticker_id,),
        )
        log.warning("sticker_media_cache_invalidated sticker_id=%s reason=%s", sticker_id, reason)

    async def _store_cache(self, sticker, data: bytes, actual_type: str) -> Path:
        path = self.cache_dir / f"{sticker['file_unique_id']}{self._suffix(actual_type)}"
        path.write_bytes(data)
        old_path = Path(sticker["local_cache_path"] or "")
        if old_path != path and old_path.is_file():
            old_path.unlink()
        await self.db.execute(
            "UPDATE stickers SET sticker_type=?,local_cache_path=?,cached_sticker_type=?,cache_byte_size=?,cache_validated_at=CURRENT_TIMESTAMP WHERE id=?",
            (actual_type, str(path), actual_type, len(data), sticker["id"]),
        )
        return path

    async def _source(self, sticker, maximum: int) -> tuple[bytes, str, bool]:
        path = Path(sticker["local_cache_path"] or "")
        if path.is_file() and path.stat().st_size <= maximum:
            data = path.read_bytes()
            actual_type = detect_sticker_type(data)
            if actual_type:
                if actual_type != sticker["sticker_type"] or sticker["cached_sticker_type"] != actual_type or path.suffix != self._suffix(actual_type):
                    await self._store_cache(sticker, data, actual_type)
                    log.warning("sticker_media_type_repaired sticker_id=%s declared=%s actual=%s", sticker["id"], sticker["sticker_type"], actual_type)
                else:
                    await self.db.execute("UPDATE stickers SET cache_byte_size=?,cache_validated_at=CURRENT_TIMESTAMP WHERE id=?", (len(data), sticker["id"]))
                log.info("sticker_media_cache_hit sticker_id=%s", sticker["id"])
                return data, actual_type, True
            await self._invalidate_cache(sticker["id"], path, reason="unsupported_signature")
        elif path.is_file():
            await self._invalidate_cache(sticker["id"], path, reason="size_limit")

        data = await self._download(sticker["file_id"], maximum)
        actual_type = detect_sticker_type(data)
        if not actual_type:
            # Do not cache an error body or malformed payload, and do not retry
            # the exact same deterministic decode failure several times.
            raise StickerMediaValidationError("downloaded sticker has unsupported media signature")
        await self._store_cache(sticker, data, actual_type)
        log.info("sticker_media_cache_miss sticker_id=%s", sticker["id"])
        return data, actual_type, False

    async def prepare_frames(self, sticker_id: int):
        """Share one source download/frame extraction across current-turn fallback and analyzer."""
        task = self._frame_tasks.get(sticker_id)
        if not task:
            task = self._frame_tasks[sticker_id] = asyncio.create_task(self._prepare_frames(sticker_id))
        return await asyncio.shield(task)

    async def _prepare_frames(self, sticker_id: int):
        sticker = await self.stickers.get_for_analysis(sticker_id)
        if not sticker: raise ValueError("sticker disappeared")
        maximum = self.settings.max_animation_bytes if sticker["sticker_type"] in {"animated", "video"} else self.settings.max_sticker_bytes
        data, actual_type, from_cache = await self._source(sticker, maximum)
        try:
            return self.extractor.extract(data, actual_type)
        except Exception as exc:
            if from_cache:
                # A payload can have a valid container signature but still be
                # truncated. Discard it once and try a fresh Telegram download.
                await self._invalidate_cache(sticker_id, Path(sticker["local_cache_path"] or ""), reason="decode_failed")
                sticker = await self.stickers.get_for_analysis(sticker_id)
                data, actual_type, _ = await self._source(sticker, maximum)
                try:
                    return self.extractor.extract(data, actual_type)
                except Exception as retry_exc:
                    raise StickerMediaValidationError(f"fresh sticker payload cannot be decoded: {type(retry_exc).__name__}") from retry_exc
            raise StickerMediaValidationError(f"downloaded sticker payload cannot be decoded: {type(exc).__name__}") from exc

    async def process_one(self) -> bool:
        row = await self.db.fetchone("SELECT * FROM sticker_analysis_jobs WHERE status='pending' ORDER BY priority DESC,id LIMIT 1")
        if not row: return False
        return await self._process(row)

    async def recover_after_restart(self):
        """A process cannot own a job after it has died; make it claimable again."""
        result = await self.db.execute("UPDATE sticker_analysis_jobs SET status='pending',updated_at=CURRENT_TIMESTAMP WHERE status='processing'")
        if result.rowcount: log.info("sticker_analysis_recovered jobs=%s", result.rowcount)
        # Previous versions trusted Telegram metadata and could cache WebM as
        # `.webp`. Requeue only rows whose real cached signature proves that
        # mismatch; unrelated failed LLM/API jobs remain failed.
        repaired = 0
        rows = await self.db.fetchall("SELECT s.* FROM stickers s JOIN sticker_analysis_jobs j ON j.sticker_id=s.id WHERE j.status='failed' AND s.local_cache_path IS NOT NULL")
        for sticker in rows:
            path = Path(sticker["local_cache_path"])
            if not path.is_file():
                continue
            actual_type = detect_sticker_type(path.read_bytes()[:16])
            if actual_type and actual_type != sticker["sticker_type"]:
                await self._store_cache(sticker, path.read_bytes(), actual_type)
                await self.db.execute("UPDATE sticker_analysis_jobs SET status='pending',attempts=0,last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE sticker_id=?", (sticker["id"],))
                repaired += 1
        if repaired:
            log.info("sticker_media_mismatch_recovered jobs=%s", repaired)

    async def process_sticker(self, sticker_id: int) -> bool:
        """Try the just-received sticker first, bounded by the handler's timeout."""
        row = await self.db.fetchone("SELECT * FROM sticker_analysis_jobs WHERE sticker_id=? AND status='pending'", (sticker_id,))
        return await self._process(row) if row else False

    async def _process(self, row) -> bool:
        job_id, sticker_id = row["id"], row["sticker_id"]
        claimed = await self.db.execute("UPDATE sticker_analysis_jobs SET status='processing',attempts=attempts+1,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'", (job_id,))
        if not claimed.rowcount: return True
        try:
            frames = await self.prepare_frames(sticker_id)
            semantics = await self.provider.analyze_sticker(frames)
            await self.stickers.save_semantics(sticker_id, semantics, analyzer_model=self.provider.model, analysis_version=self.settings.sticker_analysis_version)
            await self.db.execute("UPDATE sticker_analysis_jobs SET status='done',last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?", (job_id,))
            log.info("sticker_analysis_completed sticker_id=%s frames=%s", sticker_id, len(frames))
        except Exception as exc:
            attempts = (await self.db.fetchone("SELECT attempts FROM sticker_analysis_jobs WHERE id=?", (job_id,)))["attempts"]
            permanent = isinstance(exc, StickerMediaValidationError)
            status = "failed" if permanent or attempts >= self.settings.sticker_analysis_retry_limit else "pending"
            await self.db.execute("UPDATE sticker_analysis_jobs SET status=?,last_error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, str(exc)[:500], job_id))
            log.warning("sticker_analysis_failed sticker_id=%s attempts=%s permanent=%s error=%s", sticker_id, attempts, permanent, exc, exc_info=not permanent)
        finally:
            self._frame_tasks.pop(sticker_id, None)
        return True

    async def run(self):
        await self.recover_after_restart()
        while not self._stop.is_set():
            try:
                processed = await self.process_one()
            except Exception:
                log.exception("sticker_analysis_worker_cycle_failed")
                processed = False
            if not processed:
                try: await asyncio.wait_for(self._stop.wait(), timeout=1)
                except asyncio.TimeoutError: pass

    def stop(self): self._stop.set()
