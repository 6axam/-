"""Persistent semantic sticker retrieval with pluggable embeddings."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from random import Random

log = logging.getLogger(__name__)


class EmbeddingProviderUnavailable(RuntimeError): pass


class EmbeddingProvider(ABC):
    model_name: str
    @abstractmethod
    async def embed(self, text: str) -> list[float]: ...
    async def ensure_loaded(self) -> None: pass


class LocalSemanticEmbeddingProvider(EmbeddingProvider):
    """One process-wide multilingual SentenceTransformer, loaded only on first use."""
    _models: dict[tuple[str, str], object] = {}
    _locks: dict[tuple[str, str], asyncio.Lock] = {}

    def __init__(self, model_name: str, device: str = "cpu"):
        self.model_name, self.device = model_name, device

    async def _model(self):
        key = (self.model_name, self.device)
        if key in self._models: return self._models[key]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key not in self._models:
                try:
                    from sentence_transformers import SentenceTransformer
                    # SentenceTransformer's lazy module initialization can hang
                    # under asyncio.to_thread on this Python/Torch combination.
                    # This runs once per process; subsequent inference is off-loop.
                    self._models[key] = SentenceTransformer(self.model_name, device=self.device)
                except Exception as exc:
                    raise EmbeddingProviderUnavailable(f"Could not load local embedding model {self.model_name}: {exc}") from exc
        return self._models[key]

    async def ensure_loaded(self) -> None: await self._model()

    async def embed(self, text: str) -> list[float]:
        model = await self._model()
        try:
            # See _model: this Torch build also stalls when encode runs through
            # asyncio.to_thread. Calls are short for sticker-sized texts.
            vector = model.encode(text, normalize_embeddings=True, show_progress_bar=False)
            return [float(value) for value in vector.tolist()]
        except Exception as exc:
            raise EmbeddingProviderUnavailable(f"Local embedding inference failed: {exc}") from exc


class FakeEmbeddingProvider(EmbeddingProvider):
    """Deterministic test-only embeddings. It is never constructed by app.main."""
    model_name = "fake-semantic-v1"
    def __init__(self, dimensions: int = 8): self.dimensions = dimensions
    async def embed(self, text: str) -> list[float]:
        groups = (("проклят", "хуйня", "cursed", "horror", "what_the_fuck"), ("нежно", "поцел", "kiss", "affection", "warmth"), ("глуп", "туп", "ошиб", "facepalm", "cringe", "failure"), ("смеш", "ахах", "laughter", "mockery", "absurd"))
        lower, vector = text.lower(), [0.0] * self.dimensions
        for index, terms in enumerate(groups):
            if any(term in lower for term in terms): vector[index] = 1.0
        if not any(vector): vector[-1] = 1.0
        return vector


@dataclass(frozen=True)
class CandidateScore:
    semantic_similarity: float
    intensity_adjustment: float
    repeat_penalty: float
    consecutive_penalty: float
    final_score: float


class StickerRetriever:
    def __init__(self, db, embeddings: EmbeddingProvider, *, repeat_window_hours: int = 24, strong_window_minutes: int = 30, strategy: str = "ranked", rng: Random | None = None):
        self.db, self.embeddings = db, embeddings
        self.repeat_window_hours, self.strong_window_minutes = repeat_window_hours, strong_window_minutes
        self.strategy, self.rng, self._degraded_logged = strategy, rng or Random(), False

    async def store(self, sticker_id: int, text: str) -> bool:
        try: vector = await self.embeddings.embed(text)
        except EmbeddingProviderUnavailable:
            if not self._degraded_logged:
                log.warning("embedding_provider_degraded provider=%s", self.embeddings.model_name, exc_info=True); self._degraded_logged = True
            return False
        await self.db.execute("INSERT INTO sticker_embeddings(sticker_id,embedding_json,embedding_model) VALUES(?,?,?) ON CONFLICT(sticker_id) DO UPDATE SET embedding_json=excluded.embedding_json,embedding_model=excluded.embedding_model,created_at=CURRENT_TIMESTAMP", (sticker_id, json.dumps(vector), self.embeddings.model_name))
        return True

    async def _ensure_current_embeddings(self):
        # Lazy migration: stale hashing-semantic-v1 vectors are never compared.
        rows = await self.db.fetchall("SELECT ss.sticker_id,ss.searchable_text FROM sticker_semantics ss LEFT JOIN sticker_embeddings se ON se.sticker_id=ss.sticker_id AND se.embedding_model=? WHERE se.sticker_id IS NULL", (self.embeddings.model_name,))
        for row in rows:
            if not await self.store(row["sticker_id"], row["searchable_text"]): return False
        return True

    @staticmethod
    def _dot(left: list[float], right: list[float]) -> float: return sum(a * b for a, b in zip(left, right))
    @staticmethod
    def _tokens(value: str) -> set[str]: return set(re.findall(r"[\w_]+", value.lower(), flags=re.UNICODE))

    async def _usage_penalties(self, sticker_id: int, chat_id: int) -> tuple[float, float]:
        row = await self.db.fetchone("SELECT COUNT(*) AS uses, MIN((julianday('now')-julianday(used_at))*1440) AS minutes_ago FROM sticker_usage WHERE sticker_id=? AND chat_id=? AND direction='outgoing' AND used_at >= datetime('now', ?)", (sticker_id, chat_id, f"-{self.repeat_window_hours} hours"))
        uses, age = row["uses"] or 0, row["minutes_ago"]
        repeat = min(.26, .16 + .05 * (uses - 1)) if uses and age is not None and age <= self.strong_window_minutes else min(.12, .04 * uses)
        last = await self.db.fetchall("SELECT sticker_id FROM sticker_usage WHERE chat_id=? AND direction='outgoing' ORDER BY id DESC LIMIT 2", (chat_id,))
        consecutive = .18 if last and last[0]["sticker_id"] == sticker_id else 0.0
        if len(last) > 1 and all(item["sticker_id"] == sticker_id for item in last): consecutive += .12
        return repeat, consecutive

    @staticmethod
    def _candidate(row, score: CandidateScore) -> dict:
        return {"id": row["id"], "emoji": row["emoji"], "description": row["visual_description"], "meaning": " ".join(json.loads(row["meanings_json"]) + json.loads(row["usage_json"])), "intensity": row["intensity"], "score": score.final_score, "score_parts": score.__dict__}

    async def _fallback_search(self, query: str, limit: int, chat_id: int, intensity: float | None) -> list[dict]:
        rows = await self.db.fetchall("SELECT s.id,s.emoji,ss.visual_description,ss.meanings_json,ss.usage_json,ss.intensity FROM stickers s JOIN sticker_semantics ss ON ss.sticker_id=s.id")
        wanted, candidates = self._tokens(query), []
        for row in rows:
            tags = " ".join([row["visual_description"], *json.loads(row["meanings_json"]), *json.loads(row["usage_json"])])
            semantic = len(wanted & self._tokens(tags)) / max(1, len(wanted | self._tokens(tags)))
            repeat, consecutive = await self._usage_penalties(row["id"], chat_id)
            adjust = .08 * (1 - abs(intensity - row["intensity"])) if intensity is not None else 0.0
            candidates.append(self._candidate(row, CandidateScore(semantic, adjust, repeat, consecutive, semantic + adjust - repeat - consecutive)))
        return sorted(candidates, key=lambda item: item["score"], reverse=True)[:limit]

    async def search(self, query: str, limit: int = 8, chat_id: int | None = None, intensity: float | None = None) -> list[dict]:
        chat_id = chat_id or -1; log.info("sticker_retrieval_query query=%r provider=%s", query[:160], self.embeddings.model_name)
        try:
            ready = await self._ensure_current_embeddings(); query_vector = await self.embeddings.embed(query) if ready else None
        except EmbeddingProviderUnavailable:
            ready, query_vector = False, None
            if not self._degraded_logged:
                log.warning("embedding_provider_degraded provider=%s", self.embeddings.model_name, exc_info=True); self._degraded_logged = True
        if not ready or query_vector is None: return await self._fallback_search(query, limit, chat_id, intensity)
        rows = await self.db.fetchall("SELECT s.id,s.emoji,ss.visual_description,ss.meanings_json,ss.usage_json,ss.intensity,se.embedding_json FROM stickers s JOIN sticker_semantics ss ON ss.sticker_id=s.id JOIN sticker_embeddings se ON se.sticker_id=s.id WHERE se.embedding_model=?", (self.embeddings.model_name,))
        candidates = []
        for row in rows:
            semantic = self._dot(query_vector, json.loads(row["embedding_json"]))
            adjustment = .08 * (1 - abs(intensity - row["intensity"])) if intensity is not None else 0.0
            repeat, consecutive = await self._usage_penalties(row["id"], chat_id)
            candidates.append(self._candidate(row, CandidateScore(semantic, adjustment, repeat, consecutive, semantic + adjustment - repeat - consecutive)))
        return sorted(candidates, key=lambda item: item["score"], reverse=True)[:limit]

    def choose(self, candidates: list[dict]) -> dict | None:
        if not candidates or self.strategy == "ranked" or len(candidates) == 1: return candidates[0] if candidates else None
        top = candidates[:3]
        if top[0]["score"] - top[-1]["score"] > .06: return top[0]
        minimum = min(item["score"] for item in top)
        return self.rng.choices(top, weights=[max(.01, item["score"] - minimum + .02) for item in top], k=1)[0]
