"""Deterministic, chat-scoped long-term memory persistence and retrieval."""

import json
import logging
import re
from dataclasses import dataclass

from app.llm.schemas import MemoryCandidate

log = logging.getLogger(__name__)
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_CYRILLIC_RE = re.compile(r"^[а-яё]+$", re.IGNORECASE)
# Conservative Russian inflection endings.  This is intentionally not a
# morphological analyzer: it only covers obvious case forms while avoiding a
# broad prefix/fuzzy match between unrelated words.
_RUSSIAN_ENDINGS = (
    "иями", "ями", "ами", "ого", "ему", "ому", "ыми", "ими", "иях", "ах", "ях",
    "ов", "ев", "ей", "ам", "ям", "ом", "ем", "ой", "ей", "ую", "юю", "ия", "ья",
    "а", "я", "у", "ю", "е", "и", "ы", "о",
)


def _words(value: str) -> set[str]:
    return {word.replace("ё", "е") for word in _WORD_RE.findall(value.lower()) if len(word) >= 3}


def _normalized(value: str) -> str:
    return " ".join(_WORD_RE.findall(value.lower().replace("ё", "е")))


def _tags(value: list[str]) -> list[str]:
    return list(dict.fromkeys(tag.strip().lower().replace("ё", "е") for tag in value if tag.strip()))[:8]


def _row_tags(row) -> set[str]:
    try:
        return set(json.loads(row["tags"]))
    except (TypeError, json.JSONDecodeError):
        return set()


def _russian_stems(token: str) -> set[str]:
    """Return the token and one conservative inflection stem, if any."""
    token = token.lower().replace("ё", "е")
    if len(token) < 5 or not _CYRILLIC_RE.fullmatch(token):
        return {token}
    stems = {token}
    for ending in _RUSSIAN_ENDINGS:
        if token.endswith(ending) and len(token) - len(ending) >= 4:
            stems.add(token[:-len(ending)])
    return stems


def tokens_match(left: str, right: str) -> bool:
    """Exact-first match plus conservative Cyrillic case-form matching."""
    left = left.lower().replace("ё", "е")
    right = right.lower().replace("ё", "е")
    if left == right:
        return True
    if len(left) < 5 or len(right) < 5:
        return False
    if not (_CYRILLIC_RE.fullmatch(left) and _CYRILLIC_RE.fullmatch(right)):
        return False
    return bool(_russian_stems(left) & _russian_stems(right))


def _matched_tokens(left_words: set[str], right_words: set[str]) -> tuple[int, int]:
    """Return exact and inflection-form matches without double-counting."""
    exact_words = left_words & right_words
    exact = len(exact_words)
    unmatched_left = left_words - exact_words
    unmatched_right = right_words - exact_words
    forms = 0
    for left in unmatched_left:
        match = next((right for right in unmatched_right if tokens_match(left, right)), None)
        if match is not None:
            forms += 1
            unmatched_right.remove(match)
    return exact, forms


def _lexical_overlap(left: str, right: str) -> float:
    left_words, right_words = _words(left), _words(right)
    if not left_words or not right_words:
        return 0.0
    exact, forms = _matched_tokens(left_words, right_words)
    matched = exact + forms
    return matched / (len(left_words) + len(right_words) - matched)


@dataclass(frozen=True)
class MemoryApplyResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0


class MemoryManager:
    def __init__(self, db):
        self.db = db

    async def _all_for_chat(self, user_id: int, chat_id: int):
        return await self.db.fetchall(
            "SELECT *, (julianday('now') - julianday(COALESCE(last_used, updated_at))) * 24 AS age_hours "
            "FROM memories WHERE user_id=? AND chat_id=? ORDER BY updated_at DESC, id DESC LIMIT 200",
            (user_id, chat_id),
        )

    @staticmethod
    def _obvious_duplicate(candidate: MemoryCandidate, row) -> bool:
        if _normalized(candidate.content) == _normalized(row["content"]):
            return True
        overlap = _lexical_overlap(candidate.content, row["content"])
        tag_overlap = set(_tags(candidate.tags)) & _row_tags(row)
        return overlap >= 0.82 or (bool(tag_overlap) and overlap >= 0.55)

    async def apply(
        self,
        user_id: int,
        chat_id: int,
        candidates: list[MemoryCandidate],
        *,
        source_turn_id: int | None = None,
        retrieved_memory_ids: set[int] | None = None,
    ) -> MemoryApplyResult:
        """Apply model proposals without allowing cross-chat or arbitrary-id edits."""
        created = updated = skipped = 0
        rows = await self._all_for_chat(user_id, chat_id)
        allowed_targets = retrieved_memory_ids or set()

        for item in candidates:
            if item.decision == "IGNORE" or not item.content.strip():
                skipped += 1
                continue

            if item.decision == "SAVE":
                if any(self._obvious_duplicate(item, row) for row in rows):
                    log.debug("memory_duplicate_skipped chat_id=%s content=%r", chat_id, item.content[:80])
                    skipped += 1
                    continue
                result = await self.db.execute(
                    "INSERT INTO memories(user_id,chat_id,content,importance,confidence,tags,source_turn_id,last_used) "
                    "VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                    (user_id, chat_id, item.content.strip(), item.importance, item.confidence,
                     json.dumps(_tags(item.tags), ensure_ascii=False), source_turn_id),
                )
                row = await self.db.fetchone("SELECT * FROM memories WHERE id=?", (result.lastrowid,))
                if row:
                    rows.append(row)
                created += 1
                continue

            # A specified id must have been visible to the LLM in this exact
            # chat's RELEVANT MEMORIES block. Otherwise ignore it safely.
            target = None
            if item.target_memory_id is not None:
                if item.target_memory_id not in allowed_targets:
                    log.warning("memory_invalid_update_target chat_id=%s target_memory_id=%s", chat_id, item.target_memory_id)
                    skipped += 1
                    continue
                target = next((row for row in rows if row["id"] == item.target_memory_id), None)
            else:
                # Without an id, only infer an update from facts that were
                # actually supplied to the model for this turn. This avoids
                # mutating an unseen memory based on an ambiguous phrase.
                matches = [
                    row for row in rows
                    if row["id"] in allowed_targets and self._obvious_duplicate(item, row)
                ]
                if matches:
                    target = max(matches, key=lambda row: _lexical_overlap(item.content, row["content"]))
            if not target:
                log.warning("memory_update_without_match_skipped chat_id=%s", chat_id)
                skipped += 1
                continue

            await self.db.execute(
                "UPDATE memories SET content=?,importance=?,confidence=?,tags=?,source_turn_id=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND user_id=? AND chat_id=?",
                (item.content.strip(), item.importance, item.confidence,
                 json.dumps(_tags(item.tags), ensure_ascii=False), source_turn_id,
                 target["id"], user_id, chat_id),
            )
            refreshed = await self.db.fetchone("SELECT * FROM memories WHERE id=?", (target["id"],))
            if refreshed:
                rows[rows.index(target)] = refreshed
            updated += 1

        return MemoryApplyResult(created=created, updated=updated, skipped=skipped)

    async def relevant(self, user_id: int, chat_id: int, query: str, limit: int = 6):
        """Rank only materially matching memories; relevance beats recency."""
        query_words = _words(query)
        if not query_words:
            return []
        rows = await self._all_for_chat(user_id, chat_id)
        ranked: list[tuple[float, object]] = []
        for row in rows:
            content_exact, content_forms = _matched_tokens(query_words, _words(row["content"]))
            tag_exact, tag_forms = _matched_tokens(query_words, _row_tags(row))
            if not content_exact and not content_forms and not tag_exact and not tag_forms:
                continue
            age = float(row["age_hours"] or 0)
            recency = max(0.0, 0.15 - min(age, 720.0) / 720.0 * 0.15)
            # Exact lexical hits outrank case-form hits; tags keep their
            # stronger semantic weight without making English tags fuzzy.
            score = (content_exact * 3.0 + content_forms * 2.25 + tag_exact * 4.0
                     + tag_forms * 3.0 + row["importance"] + row["confidence"] + recency)
            ranked.append((score, row))
        ranked.sort(key=lambda pair: (pair[0], pair[1]["updated_at"], pair[1]["id"]), reverse=True)
        selected = [row for _, row in ranked[:limit]]
        for row in selected:
            await self.db.execute("UPDATE memories SET last_used=CURRENT_TIMESTAMP WHERE id=? AND chat_id=?", (row["id"], chat_id))
        return selected
