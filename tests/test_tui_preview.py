from datetime import datetime, timezone

import pytest

from app.tui.app import AnyaTamagotchiApp
from app.tui.preview import PreviewSession
from app.tui.scenes import SceneKind


def test_preview_session_uses_synthetic_state_only():
    preview = PreviewSession("idle_home", "night", "warm", 0.70)
    snapshot = preview.snapshot()
    assert snapshot.chat_id == 0
    assert snapshot.lifecycle == "preview"
    assert snapshot.presence.local_time == "23:00"
    assert snapshot.emotions == {"affection": pytest.approx(0.70)}
    assert preview.decision.kind is SceneKind.IDLE_HOME
    assert "scene=IDLE_HOME" in preview.diagnostic(24, 12)


def test_preview_controls_change_only_in_memory_configuration():
    preview = PreviewSession()
    preview.cycle_time()
    preview.cycle_emotion()
    preview.adjust_intensity(0.8)
    preview.select_scene("sleeping")
    assert preview.time_of_day == "night"
    assert preview.emotion == "happy"
    assert preview.intensity == 1.0
    assert preview.decision.kind is SceneKind.SLEEPING


@pytest.mark.asyncio
async def test_preview_app_mounts_without_a_reader_and_runs_preview_animation():
    app = AnyaTamagotchiApp(None, preview=PreviewSession("idle_home", "night", "anger", 0.8))
    async with app.run_test(size=(80, 24)):
        app._start_preview()
        initial_frame = app.frame
        app._animate_preview()
        assert app.snapshot is not None and app.snapshot.chat_id == 0
        assert app.decision is not None and app.decision.kind is SceneKind.IDLE_HOME
        assert app.frame == initial_frame + 1
        assert "scene=IDLE_HOME" in app.query_one("#preview_diagnostic").renderable.plain
        app.action_show_emotions()
        assert app.preview is not None and app.preview.emotion == "jealousy"
        app.action_preview_pause()
        paused_frame = app.frame
        app._animate_preview()
        assert app.frame == paused_frame
        app.exit()
