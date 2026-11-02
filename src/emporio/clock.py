"""Time source for the agent: store local time, optionally frozen.

Opening hours, return windows and "is the delivery late?" all depend on today's date.
Freezing the clock (``EMPORIO_NOW=2026-02-20T10:00``) makes demos and tests reproducible;
the sample data ends in March 2026, so a frozen date inside that range keeps it meaningful.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

STORE_TIMEZONE = "America/Campo_Grande"
NOW_ENV = "EMPORIO_NOW"


class Clock:
    def __init__(self, fixed: datetime | None = None, timezone: str = STORE_TIMEZONE):
        self.tz = ZoneInfo(timezone)
        if fixed is not None and fixed.tzinfo is None:
            fixed = fixed.replace(tzinfo=self.tz)  # naive values are store local time
        self._fixed = fixed

    @classmethod
    def from_env(cls) -> "Clock":
        value = os.environ.get(NOW_ENV)
        return cls(datetime.fromisoformat(value) if value else None)

    @property
    def is_frozen(self) -> bool:
        return self._fixed is not None

    def now(self) -> datetime:
        return self._fixed.astimezone(self.tz) if self._fixed else datetime.now(self.tz)

    def today(self) -> date:
        return self.now().date()
