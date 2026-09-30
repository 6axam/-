class StickerClassifier:
    """Vision-classifier extension point. v1 retains Telegram emoji and user usage."""
    async def classify(self, _sticker) -> tuple[str, list[str]]:
        return "", []
