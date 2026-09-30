from app.llm.schemas import MemoryCandidate


class MemoryExtractor:
    """Boundary for a later dedicated extraction LLM call.

    In v1 candidates arrive in the structured conversational response, so this
    adapter keeps that decision independent from persistence.
    """
    def extract(self, candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
        return [candidate for candidate in candidates if candidate.decision != "IGNORE"]
