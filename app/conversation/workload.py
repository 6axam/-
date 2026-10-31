"""Read-only, bounded view of Anya's shared conversational workload."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkloadSnapshot:
    active_generations: int
    pending_messages: int
    pending_reads: int
    pending_responses: int
    in_flight_actions: int
    unfinished_conversations: int

    @property
    def pressure(self) -> str:
        score = self.active_generations + self.pending_messages + self.pending_reads + self.pending_responses + self.in_flight_actions
        if score >= 7:
            return "high"
        if score >= 3:
            return "moderate"
        return "low"


class ConversationWorkload:
    """No scheduler of its own: delivery remains owned by existing workers."""

    def __init__(self, db, *, transport: str, queue=None):
        self.db, self.transport, self.queue, self.manager = db, transport, queue, None

    def bind_manager(self, manager) -> None:
        """Read active LLM work without becoming a second scheduler."""
        self.manager = manager

    async def snapshot(self, current_chat_id: int) -> WorkloadSnapshot:
        unread = await self.db.fetchone(
            "SELECT COUNT(*) AS count FROM messages WHERE sender='user' AND internally_read_at IS NULL AND chat_id<>?",
            (current_chat_id,),
        )
        reads = await self.db.fetchone(
            "SELECT COUNT(*) AS count FROM scheduled_reads WHERE transport=? AND status IN ('pending','processing') AND chat_id<>?",
            (self.transport, current_chat_id),
        )
        responses = await self.db.fetchone(
            "SELECT COUNT(*) AS count FROM scheduled_responses WHERE transport=? AND status IN ('pending','processing') AND chat_id<>?",
            (self.transport, current_chat_id),
        )
        unfinished = await self.db.fetchone(
            "SELECT COUNT(*) AS count FROM conversation_lifecycle WHERE chat_id<>? AND expects_reply=1 AND conversation_status IN ('active','cooling_down')",
            (current_chat_id,),
        )
        active_generations = 0
        if self.manager:
            active_generations = sum(
                1 for chat_id, task in self.manager.requests.items()
                if chat_id != current_chat_id and not task.done()
            )
        in_flight = 0
        if self.queue:
            in_flight = sum(1 for chat_id, task in self.queue._workers.items() if chat_id != current_chat_id and not task.done())
        return WorkloadSnapshot(
            active_generations=active_generations, pending_messages=int(unread["count"] or 0), pending_reads=int(reads["count"] or 0),
            pending_responses=int(responses["count"] or 0), in_flight_actions=in_flight,
            unfinished_conversations=int(unfinished["count"] or 0),
        )
