EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),  # modern emoji, flags, skin tones
    (0x2600, 0x27BF),   # miscellaneous symbols and dingbats
)
EMOJI_CODEPOINTS = {0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122, 0x2139, 0x3030, 0x303D, 0x3297, 0x3299, 0xFE0F, 0x200D, 0x20E3}


def strip_emoji(text: str) -> str:
    """Remove Unicode emoji while leaving normal Russian/Latin text intact."""
    return "".join(
        char for char in text
        if ord(char) not in EMOJI_CODEPOINTS and not any(low <= ord(char) <= high for low, high in EMOJI_RANGES)
    )
