"""Small cached weather client. Absence of data never becomes invented weather."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import httpx

log = logging.getLogger(__name__)

@dataclass(frozen=True)
class WeatherSnapshot:
    temperature_c: float
    apparent_temperature_c: float
    weather_code: int
    cloud_cover: int
    precipitation_mm: float
    is_day: bool

    def describe(self) -> str:
        parts = [f"{self.temperature_c:.0f}°C (ощущается как {self.apparent_temperature_c:.0f}°C)"]
        if self.precipitation_mm > 0: parts.append("сейчас есть осадки")
        elif self.cloud_cover < 25: parts.append("ясно")
        elif self.cloud_cover > 75: parts.append("пасмурно")
        else: parts.append("переменная облачность")
        return "; ".join(parts)

class WeatherService:
    def __init__(self, latitude: float, longitude: float, *, enabled: bool = True, ttl_minutes: int = 20):
        self.latitude, self.longitude, self.enabled = latitude, longitude, enabled
        self.ttl = timedelta(minutes=ttl_minutes); self._cached = None; self._cached_at = None

    async def current(self) -> WeatherSnapshot | None:
        if not self.enabled: return None
        now = datetime.now(timezone.utc)
        if self._cached and self._cached_at and now - self._cached_at < self.ttl: return self._cached
        try:
            params = {"latitude": self.latitude, "longitude": self.longitude,
                "current": "temperature_2m,apparent_temperature,weather_code,cloud_cover,precipitation,is_day"}
            async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=3)) as client:
                response = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
            response.raise_for_status(); c = response.json()["current"]
            self._cached = WeatherSnapshot(float(c["temperature_2m"]), float(c["apparent_temperature"]), int(c["weather_code"]), int(c["cloud_cover"]), float(c["precipitation"]), bool(c["is_day"]))
            self._cached_at = now; return self._cached
        except Exception:
            log.warning("weather_unavailable", exc_info=True); return self._cached
