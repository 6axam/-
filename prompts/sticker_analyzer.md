# Sticker semantic analyzer

Analyze one Telegram sticker as visual communication. Return a short, strict JSON object with literal visual description, animation/movement when present, characters, emotions, conversational meanings, natural usage situations, and intensity from 0 to 1.

If multiple images are supplied, they are chronological frames of one animated sticker. Interpret their movement as a sequence. Do not invent text or details that are not visible. Focus on the pragmatic meaning someone intends in chat, not only what is drawn.
