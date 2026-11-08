"""Timezone helpers. Asia/Manila is explicit everywhere; the system timezone is never used."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Manila")


class Clock:
    def now(self) -> datetime:
        return datetime.now(TZ)


class FakeClock(Clock):
    def __init__(self, start: datetime):
        self._now = ensure_aware(start)

    def now(self) -> datetime:
        return self._now

    def set(self, dt: datetime) -> None:
        self._now = ensure_aware(dt)

    def advance(self, **kw) -> None:
        self._now += timedelta(**kw)


def ensure_aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


def hhmm(s: str) -> time:
    h, m = s.strip().split(":")
    return time(int(h), int(m))


def at(d: date, s: str) -> datetime:
    return datetime.combine(d, hhmm(s), tzinfo=TZ)


def shift_quiet(dt: datetime, start_hour: int = 23, end_hour: int = 6, shift_hour: int = 22) -> datetime:
    """Quiet hours: move EARLIER to shift_hour (never later - the window might be closed by then)."""
    dt = ensure_aware(dt)
    if dt.hour >= start_hour:
        return dt.replace(hour=shift_hour, minute=0, second=0, microsecond=0)
    if dt.hour < end_hour:
        prev = dt - timedelta(days=1)
        return prev.replace(hour=shift_hour, minute=0, second=0, microsecond=0)
    return dt


def in_quiet(dt: datetime, start_hour: int = 23, end_hour: int = 6) -> bool:
    dt = ensure_aware(dt)
    return dt.hour >= start_hour or dt.hour < end_hour


def window_open(last_inbound: datetime | None, now: datetime, limit_hours: float = 23) -> bool:
    if last_inbound is None:
        return False
    return now < ensure_aware(last_inbound) + timedelta(hours=limit_hours)


def fmt_time(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def fmt_day(d: date | datetime) -> str:
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime("%a, %b %d").replace(" 0", " ")
