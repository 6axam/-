"""Launch with: python -m app.tui"""

from app.config import load_settings
from app.tui.app import AnyaTamagotchiApp
from app.tui.state import ReadOnlyStateReader


def main() -> None:
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
