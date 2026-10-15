"""Launch: python -m app.monitor"""
from app.config import load_settings
from app.monitor.app import AnyaMonitorApp
from app.monitor.reader import MonitorReader


def main() -> None:
    settings = load_settings()
    AnyaMonitorApp(MonitorReader(settings.database_url, settings.owner_telegram_id)).run()


if __name__ == "__main__":
    main()
