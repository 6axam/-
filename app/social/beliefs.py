"""Provenance-aware evidence and deliberately conservative belief revision."""
from __future__ import annotations

from dataclasses import dataclass

from app.memory.manager import _lexical_overlap


@dataclass(frozen=True)
class EvidenceInput:
    topic: str
    proposition: str
    evidence_kind: str
    reliability: float
    independence_key: str
    social_event_id: int | None = None
    provenance: dict | None = None


@dataclass(frozen=True)
class BeliefProposal:
    topic: str
    proposition: str
    stance: str
    confidence: float
    importance: float
    emotional_significance: float = .0
    origin: str = "own_reflection"
    provenance: dict | None = None


class BeliefRevisionEngine:
    """Store claims as evidence; revise only through explicit, qualified input."""

    revisable_evidence_kinds = frozenset({"verified_fact", "own_reflection"})

    def __init__(self, db):
        self.db = db

    async def record_evidence(self, evidence: EvidenceInput) -> int | None:
        return await self.db.record_social_evidence(
            social_event_id=evidence.social_event_id, topic=evidence.topic.strip(),
            proposition=evidence.proposition.strip(), evidence_kind=evidence.evidence_kind,
            reliability=max(0., min(1., evidence.reliability)),
            independence_key=evidence.independence_key.strip(), provenance=evidence.provenance or {},
        )

    async def form_belief(self, proposal: BeliefProposal) -> int | None:
        # External observation/claim is deliberately incapable of reaching this
        # API. A future appraisal must make an explicit own-reflection proposal.
        if proposal.origin not in {"own_reflection", "curated_seed"}:
            return None
        return await self.db.create_social_belief(
            topic=proposal.topic.strip(), proposition=proposal.proposition.strip(), stance=proposal.stance,
            confidence=max(0., min(.70, proposal.confidence)), importance=max(0., min(1., proposal.importance)),
            emotional_significance=max(0., min(1., proposal.emotional_significance)), origin=proposal.origin,
            provenance=proposal.provenance or {},
        )

    async def revise(self, belief_id: int, evidence_id: int, relation: str) -> bool:
        belief, evidence = await self.db.social_belief(belief_id), await self.db.social_evidence(evidence_id)
        if not belief or not evidence or relation not in {"support", "contradict"}:
            return False
        if evidence["evidence_kind"] not in self.revisable_evidence_kinds or evidence["status"] != "active":
            return False
        weight = min(.15, .03 + .12 * float(evidence["reliability"]))
        if not await self.db.link_social_belief_evidence(
            belief_id=belief_id, evidence_id=evidence_id, relation=relation, weight=weight,
            independence_key=evidence["independence_key"],
        ):
            return False
        old_confidence, old_stance = float(belief["confidence"]), belief["stance"]
        new_confidence = max(0., min(1., old_confidence + (weight if relation == "support" else -weight)))
        new_stance = "uncertain" if new_confidence < .20 else old_stance
        await self.db.revise_social_belief(
            belief_id=belief_id, evidence_id=evidence_id, old_confidence=old_confidence,
            new_confidence=new_confidence, old_stance=old_stance, new_stance=new_stance,
            reason=f"{relation}:{evidence['evidence_kind']}",
        )
        return True

    async def retract_event_evidence(self, source_row_id: int, event_ids: tuple[str, ...]) -> int:
        """Undo qualified evidence effects when its permitted source is removed."""
        retracted = 0
        for evidence in await self.db.active_social_evidence_for_events(source_row_id, event_ids):
            for link in await self.db.social_belief_evidence_links(evidence["id"]):
                old_confidence, old_stance = float(link["confidence"]), link["stance"]
                change = float(link["weight"])
                new_confidence = max(0., min(1., old_confidence - change if link["relation"] == "support" else old_confidence + change))
                new_stance = "uncertain" if new_confidence < .20 else old_stance
                await self.db.revise_social_belief(
                    belief_id=link["belief_id"], evidence_id=evidence["id"], old_confidence=old_confidence,
                    new_confidence=new_confidence, old_stance=old_stance, new_stance=new_stance,
                    reason="evidence_retracted",
                )
            retracted += int(await self.db.retract_social_evidence(evidence["id"]))
        return retracted


class BeliefRetrieval:
    """Read only Anya's active positions, never raw external evidence."""

    def __init__(self, db):
        self.db = db

    async def search(self, query: str, limit: int = 4):
        ranked = []
        for row in await self.db.active_social_beliefs():
            score = _lexical_overlap(query, f"{row['topic']} {row['proposition']}")
            if score > 0:
                ranked.append((score, row["importance"], row["confidence"], row))
        ranked.sort(key=lambda item: item[:3], reverse=True)
        return [item[3] for item in ranked[:max(0, limit)]]
