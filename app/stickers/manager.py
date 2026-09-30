import json
import logging
import re

from app.actions.models import StickerIntent
from app.llm.schemas import StickerSemantics
from app.stickers.retriever import StickerRetriever

log = logging.getLogger(__name__)


class StickerManager:
    """Persistent media catalog, separate from personal conversational memory."""
    def __init__(self, db, embeddings, *, repeat_window_hours: int = 24, strong_window_minutes: int = 30, selection_strategy: str = "ranked", rng=None):
        self.db = db
        self.retriever = StickerRetriever(db, embeddings, repeat_window_hours=repeat_window_hours, strong_window_minutes=strong_window_minutes, strategy=selection_strategy, rng=rng)

    @staticmethod
    def sticker_type(sticker) -> str:
        if getattr(sticker, "is_animated", False): return "animated"
        if getattr(sticker, "is_video", False): return "video"
        return "static"

    async def import_set(self, sticker_set, *, current_file_unique_id: str | None = None):
        existing = await self.db.fetchone("SELECT name FROM sticker_sets WHERE name=?", (sticker_set.name,))
        rows = [(sticker.file_id, sticker.file_unique_id, sticker.emoji or "", self.sticker_type(sticker)) for sticker in sticker_set.stickers]
        queued = await self.db.import_sticker_pack(name=sticker_set.name, title=sticker_set.title, stickers=rows, current_unique_id=current_file_unique_id)
        log.info("sticker_pack_imported set_name=%s existing=%s queued=%s", sticker_set.name, bool(existing), queued)

    async def queue_analysis(self, sticker_id: int, priority: int = 0):
        await self.db.execute("INSERT INTO sticker_analysis_jobs(sticker_id,status,priority) VALUES(?,'pending',?) ON CONFLICT(sticker_id) DO UPDATE SET priority=MAX(priority,excluded.priority), status=CASE WHEN sticker_analysis_jobs.status='failed' THEN 'pending' ELSE sticker_analysis_jobs.status END, updated_at=CURRENT_TIMESTAMP", (sticker_id, priority))
        log.info("sticker_analysis_queued sticker_id=%s priority=%s", sticker_id, priority)

    async def seen(self, sticker, chat_id: int | None = None):
        await self.db.execute("UPDATE stickers SET times_seen=times_seen+1 WHERE file_unique_id=?", (sticker.file_unique_id,))
        row = await self.db.fetchone("SELECT id FROM stickers WHERE file_unique_id=?", (sticker.file_unique_id,))
        if row and chat_id is not None: await self.record_usage(row["id"], chat_id, "incoming")

    async def register_incoming(self, sticker):
        """A sticker without a pack still gets a semantic record and can be understood."""
        await self.db.execute("INSERT OR IGNORE INTO stickers(file_id,file_unique_id,set_name,emoji,sticker_type) VALUES(?,?,?,?,?)", (sticker.file_id, sticker.file_unique_id, getattr(sticker, "set_name", None), sticker.emoji or "", self.sticker_type(sticker)))
        row = await self.db.fetchone("SELECT id FROM stickers WHERE file_unique_id=?", (sticker.file_unique_id,))
        if not await self.db.fetchone("SELECT sticker_id FROM sticker_semantics WHERE sticker_id=?", (row["id"],)):
            await self.queue_analysis(row["id"], 100)
        return row["id"]

    async def file_id(self, sticker_id: int):
        row = await self.db.fetchone("SELECT file_id FROM stickers WHERE id=?", (sticker_id,)); return row["file_id"] if row else None

    async def get_for_analysis(self, sticker_id: int): return await self.db.fetchone("SELECT * FROM stickers WHERE id=?", (sticker_id,))

    async def save_semantics(self, sticker_id: int, semantics: StickerSemantics, *, analyzer_model: str, analysis_version: str):
        await self.db.execute("INSERT INTO sticker_semantics(sticker_id,visual_description,animation_description,characters_json,emotions_json,meanings_json,usage_json,intensity,searchable_text,analyzer_model,analysis_version) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(sticker_id) DO UPDATE SET visual_description=excluded.visual_description,animation_description=excluded.animation_description,characters_json=excluded.characters_json,emotions_json=excluded.emotions_json,meanings_json=excluded.meanings_json,usage_json=excluded.usage_json,intensity=excluded.intensity,searchable_text=excluded.searchable_text,analyzer_model=excluded.analyzer_model,analysis_version=excluded.analysis_version,analyzed_at=CURRENT_TIMESTAMP", (sticker_id, semantics.visual_description, semantics.animation_description, json.dumps(semantics.characters), json.dumps(semantics.emotions), json.dumps(semantics.meanings), json.dumps(semantics.usage), semantics.intensity, semantics.searchable_text(), analyzer_model, analysis_version))
        await self.db.execute("UPDATE sticker_sets SET analyzed_stickers=(SELECT COUNT(*) FROM stickers s JOIN sticker_semantics ss ON ss.sticker_id=s.id WHERE s.set_name=sticker_sets.name), analysis_status=CASE WHEN (SELECT COUNT(*) FROM stickers s JOIN sticker_semantics ss ON ss.sticker_id=s.id WHERE s.set_name=sticker_sets.name) >= total_stickers THEN 'done' ELSE 'processing' END WHERE name=(SELECT set_name FROM stickers WHERE id=?)", (sticker_id,))
        await self.retriever.store(sticker_id, semantics.searchable_text())

    async def semantic_for_unique_id(self, file_unique_id: str) -> str | None:
        row = await self.db.fetchone("SELECT visual_description,animation_description,meanings_json FROM sticker_semantics ss JOIN stickers s ON s.id=ss.sticker_id WHERE s.file_unique_id=?", (file_unique_id,))
        if not row: return None
        motion = f"; движение: {row['animation_description']}" if row["animation_description"] else ""
        return f"Максим отправил стикер: {row['visual_description']}{motion}. Смысл: {', '.join(json.loads(row['meanings_json']))}."

    @staticmethod
    def _tokens(value: str) -> set[str]: return set(re.findall(r"[\w_]+", value.lower(), flags=re.UNICODE))

    async def candidates(self, text: str, limit: int = 8, chat_id: int | None = None, intensity: float | None = None):
        embedded = await self.retriever.search(text, limit=limit, chat_id=chat_id, intensity=intensity)
        if embedded:
            log.info("sticker_candidates_found query=%r count=%s", text[:120], len(embedded)); return embedded
        rows = await self.db.fetchall("SELECT s.id,s.emoji,ss.visual_description,ss.animation_description,ss.meanings_json,ss.usage_json,(SELECT COUNT(*) FROM sticker_usage u WHERE u.sticker_id=s.id AND u.chat_id=?) AS recent_uses FROM stickers s JOIN sticker_semantics ss ON ss.sticker_id=s.id", (chat_id or -1,))
        query, scored = self._tokens(text), []
        for row in rows:
            meaning = " ".join(json.loads(row["meanings_json"]) + json.loads(row["usage_json"]))
            semantic = self._tokens(" ".join([row["visual_description"], row["animation_description"] or "", meaning]))
            similarity = len(query & semantic) / max(1, len(query | semantic))
            scored.append((similarity - min(.18, row["recent_uses"] * .06), row, meaning))
        scored.sort(key=lambda item: item[0], reverse=True)
        result = [{"id": row["id"], "emoji": row["emoji"], "description": row["visual_description"], "meaning": meaning, "score": round(score, 4)} for score, row, meaning in scored[:limit]]
        log.info("sticker_candidates_found query=%r count=%s", text[:120], len(result)); return result

    async def resolve_intent(self, intent: StickerIntent, chat_id: int) -> int | None:
        candidates = await self.candidates(" ".join(part for part in [intent.meaning, intent.emotion or ""] if part), limit=8, chat_id=chat_id, intensity=intent.intensity)
        if not candidates:
            log.info("sticker_selection_failed chat_id=%s reason=no_semantic_candidates", chat_id); return None
        chosen = self.retriever.choose(candidates)
        log.info("sticker_selected chat_id=%s sticker_id=%s strategy=%s score_parts=%s", chat_id, chosen["id"], self.retriever.strategy, chosen["score_parts"])
        return chosen["id"]

    async def record_usage(self, sticker_id: int, chat_id: int, direction: str, context: str | None = None):
        await self.db.execute("INSERT INTO sticker_usage(sticker_id,chat_id,direction,context) VALUES(?,?,?,?)", (sticker_id, chat_id, direction, context))
