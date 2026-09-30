import random
from app.actions.models import Duration


class TimingEngine:
    ranges = {Duration.instant: (.1, .4), Duration.short: (.5, 1.5), Duration.medium: (1.5, 4), Duration.long: (4, 12)}

    def pause(self, duration: Duration) -> float:
        low, high = self.ranges[duration]
        return random.uniform(low, high)

    def typing_seconds(self, text: str) -> float:
        # Ограничение не даёт очень длинному сообщению «печататься» вечность.
        return min(5.0, max(.35, len(text) / 35 + random.uniform(.05, .35)))

    def between_messages(self, next_text: str) -> float:
        """A short cadence delay whose duration tracks the next thought."""
        return min(3.0, max(.4, len(next_text) / 45 + random.uniform(.15, .6)))
