"""Launch with: python -m app.tui [--preview idle_home]"""

import argparse

from app.config import load_settings
from app.tui.app import AnyaTamagotchiApp
from app.tui.preview import PREVIEW_EMOTIONS, PREVIEW_SCENES, PREVIEW_TIMES, PreviewSession
from app.tui.state import ReadOnlyStateReader


def main() -> None:
    parser = argparse.ArgumentParser(description="Anya's read-only terminal tamagotchi")
    parser.add_argument("--preview", choices=tuple(PREVIEW_SCENES), help="Run a synthetic visual preview without opening production state")
    parser.add_argument("--time", choices=PREVIEW_TIMES, default="evening", help="Preview time-of-day lighting")
    parser.add_argument("--emotion", choices=PREVIEW_EMOTIONS, default="warm", help="Preview dominant emotion")
    parser.add_argument("--intensity", type=float, default=0.70, help="Preview emotion intensity, from 0.0 to 1.0")
    args = parser.parse_args()
    if args.preview:
        preview = PreviewSession(args.preview, args.time, args.emotion, args.intensity)
        AnyaTamagotchiApp(None, preview=preview).run()
        return
    settings = load_settings()
    reader = ReadOnlyStateReader(
        settings.database_url,
        settings.owner_telegram_id,
        timezone_name=settings.timezone,
        sleep_start=settings.sleep_start_hour,
        wake_hour=settings.wake_hour,
        college_start_hour=settings.college_start_hour,
        college_end_hour=settings.college_end_hour,
        college_weekdays=settings.college_weekdays,
    )
    AnyaTamagotchiApp(reader).run()


if __name__ == "__main__":
    main()
