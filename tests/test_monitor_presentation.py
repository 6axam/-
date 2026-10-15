from rich.console import Console

from app.monitor.views import _delta_rows, history, overview
from app.monitor.widgets import leader_row


def render(value, width=64):
    console = Console(width=width, record=True, force_terminal=False)
    console.print(value)
    return console.export_text()


def test_leader_row_fills_width_and_right_aligns_unicode_value():
    text = render(leader_row("социальная безопасность", "+4%"), 54).splitlines()[0]
    assert "социальная безопасность" in text and "─" in text and text.endswith("+4%")


def test_leader_row_truncates_narrow_unicode_label():
    text = render(leader_row("важность продолжения очень длинного разговора", "25%"), 22)
    assert "…" in text and text.rstrip().endswith("25%")


def test_deltas_use_before_after_and_human_dialogue_booleans():
    item = {"delta": {"emotions": {"hurt": .17}, "lifecycle": {"expects_reply": 1, "followup_importance": .15}},
            "before": {"emotions": {"hurt": .31}, "lifecycle": {"expects_reply": 0, "followup_importance": .1}},
            "after": {"emotions": {"hurt": .48}, "lifecycle": {"expects_reply": 1, "followup_importance": .25}}}
    text = render(_delta_rows(item, details=True, include_dialogue=True))
    assert "31% → 48%" in text and "НЕТ → ДА" in text and "важность продолжения" in text
    assert "expects_reply" not in text and "followup importance" not in text


def test_tiny_deltas_are_filtered_and_overview_excludes_dialogue_metadata():
    item = {"created_at": "2026-10-09 12:00:00", "user_message": "да ну тебя", "delta": {"emotions": {"hurt": .005, "joy": .1}, "lifecycle": {"expects_reply": 1}}, "before": {}, "after": {}}
    assert "обида" not in render(_delta_rows(item))
    rendered = render(overview({"last_generation": {}, "lifecycle": {}, "last_message": {}, "emotions": {}, "relationship": {}, "observations": [item]}), 100)
    assert "ожидает ответа" not in rendered
