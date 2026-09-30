from app.telegram.text import strip_emoji


def test_strip_emoji_keeps_plain_text_and_punctuation():
    assert strip_emoji("бля 😀 ну что, работает? ❤️") == "бля  ну что, работает? "
