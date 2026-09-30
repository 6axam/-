class ResponseTimingEngine:
    """Backend-owned timing: LLM chooses semantics, never seconds."""
    ranges = {"urgent": (1, 3), "normal": (20, 90), "low": (180, 900)}
    def delay(self, urgency: str, state=None, active_conversation: bool = False, pending_messages: int = 1) -> float:
        low, high = self.ranges.get(urgency, self.ranges["normal"])
        delay = (low + high) / 2
        if state:
            if state["availability"] == "busy": delay *= 1.5
            elif state["availability"] == "away": delay *= 2
            if state["conversation_interest"] > .75: delay *= .8
        # During an active exchange, human-like rhythm is still much faster
        # than an "away" reply. More buffered messages should coalesce, not
        # create a longer chain of independent future replies.
        if active_conversation:
            delay = min(delay, 5 if urgency != "low" else 15)
        if pending_messages > 1:
            delay *= .85
        return delay
