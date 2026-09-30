class StickerImporter:
    def __init__(self, manager): self.manager = manager
    async def import_sticker_set(self, sticker_set):
        await self.manager.import_set(sticker_set)
