"""Explicit, fail-closed sharing of selected private-contact facts."""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.memory.manager import _lexical_overlap


VISIBILITIES = frozenset({"private", "public", "shareable", "restricted"})


@dataclass(frozen=True)
class SharedContactFact:
    memory_id: int
    owner_user_id: int
    content: str
    visibility: str
    username: str | None
    importance: float
    confidence: float


class ContactMemorySharing:
    """Policy boundary for facts one contact explicitly permits to be reused.

    The LLM never calls this class. A missing row, malformed audience, revoked
    grant, `private`, and `restricted` are all non-shareable by construction.
    """

    def __init__(self, db):
        self.db = db

    @staticmethod
    def _audience(values) -> tuple[int, ...]:
        result = []
        for value in values or ():
            if not isinstance(value, int) or value <= 0:
                raise ValueError("audience IDs must be positive integers")
            if value not in result:
                result.append(value)
        return tuple(result)

    async def grant(self, *, memory_id: int, owner_user_id: int, visibility: str,
                    allowed_audience=()) -> bool:
        if visibility not in VISIBILITIES:
            raise ValueError("unsupported contact memory visibility")
        owner = await self.db.contact_memory_for_owner(memory_id, owner_user_id)
        if owner is None:
            return False
        audience = self._audience(allowed_audience)
        if visibility == "shareable" and not audience:
            raise ValueError("shareable facts need an explicit audience")
        if visibility in {"private", "restricted"}:
            audience = ()
        await self.db.set_contact_memory_share(
            memory_id=memory_id, owner_user_id=owner_user_id, visibility=visibility,
            allowed_audience_json=json.dumps(audience),
        )
        return True

    async def revoke(self, *, memory_id: int, owner_user_id: int) -> bool:
        return await self.db.revoke_contact_memory_share(
            memory_id=memory_id, owner_user_id=owner_user_id,
        )

    async def relevant_for(self, *, audience_user_id: int, query: str, limit: int = 4) -> list[SharedContactFact]:
        """Recheck scope immediately before a fact can enter another context."""
        ranked = []
        for row in await self.db.contact_memory_share_candidates(audience_user_id):
            try:
                audience = tuple(json.loads(row["allowed_audience_json"] or "[]"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            visibility = row["visibility"]
            allowed = visibility == "public" or (
                visibility == "shareable" and audience_user_id in audience
            )
            if not allowed:
                continue
            score = _lexical_overlap(query, row["content"])
            if score <= 0:
                continue
            fact = SharedContactFact(
                memory_id=int(row["id"]), owner_user_id=int(row["owner_user_id"]),
                content=row["content"], visibility=visibility, username=row["username"],
                importance=float(row["importance"]), confidence=float(row["confidence"]),
            )
            ranked.append((score, fact.importance, fact.confidence, fact))
        ranked.sort(key=lambda item: item[:3], reverse=True)
        return [item[3] for item in ranked[:max(0, limit)]]
