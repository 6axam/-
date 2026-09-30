class MemoryRetrieval:
    """Keyword-backed v1 retrieval; can be replaced by embeddings without callers changing."""
    def __init__(self, manager): self.manager = manager
    async def search(self, user_id: int, query: str, limit: int = 6):
        return await self.manager.relevant(user_id, query, limit)
