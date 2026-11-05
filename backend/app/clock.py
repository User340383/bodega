"""Injectable application clock: "today" is the business date in APP_TIMEZONE."""
import os
from datetime import date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

DEFAULT_TIMEZONE = "UTC"


class Clock(Protocol):
    def today(self) -> date: ...


class SystemClock:
    def __init__(self, timezone_name: str | None = None):
        self._zone = ZoneInfo(timezone_name or os.getenv("APP_TIMEZONE") or DEFAULT_TIMEZONE)

    def today(self) -> date:
        return datetime.now(self._zone).date()


def get_clock() -> Clock:
    """FastAPI dependency; tests replace it through app.dependency_overrides."""
    try:
        return SystemClock()
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise HTTPException(503, "Application timezone is not configured correctly.") from exc
