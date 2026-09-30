class ResponseTimingEngine:
    """Backend-owned timing: LLM chooses semantics, never seconds."""
    ranges = {"urgent": (1, 3), "normal": (20, 90), "low": (180, 900)}
    def __init__(self, college_normal_delay_multiplier: float = 1.5, college_active_delay_cap_seconds: float = 30.0):
        self.college_normal_delay_multiplier = college_normal_delay_multiplier
        self.college_active_delay_cap_seconds = college_active_delay_cap_seconds

    def delay(self, urgency: str, state=None, active_conversation: bool = False, pending_messages: int = 1,
              *, daily_phase: str = "free", event_availability: str | None = None) -> float:
        low, high = self.ranges.get(urgency, self.ranges["normal"])
        delay = (low + high) / 2
        if state:
            if state["conversation_interest"] > .75: delay *= .8
        # Only a real persisted daily event can make an otherwise free
        # afternoon/evening reply substantially slower.
        if event_availability == "busy":
            delay *= 1.5
        elif event_availability == "away":
            delay *= 2.0
        elif daily_phase == "college" and urgency == "normal":
            delay *= self.college_normal_delay_multiplier
        # During an active exchange, human-like rhythm is still much faster
        # than an "away" reply. More buffered messages should coalesce, not
        # create a longer chain of independent future replies.
        if active_conversation:
            cap = 5 if urgency != "low" else 15
            if daily_phase == "college" and urgency == "normal":
                cap = self.college_active_delay_cap_seconds
            delay = min(delay, cap)
        if pending_messages > 1:
            delay *= .85
        return delay
