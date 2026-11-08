"""Day modes: work | aral | off | leave | holiday. Active = bot runs the normal daily flow."""
from __future__ import annotations

from datetime import date

import holidays
from sqlalchemy.orm import Session

from app.db.models import DayMode

ACTIVE = {"work", "aral"}
LABEL = {"work": "💼 Work", "aral": "📚 Aral", "off": "😴 Off", "leave": "🏖️ Leave", "holiday": "🎉 Holiday"}

_cache: dict[int, holidays.HolidayBase] = {}


def ph_holidays(year: int):
    if year not in _cache:
        _cache[year] = holidays.country_holidays("PH", years=year)
    return _cache[year]


def holiday_name(d: date) -> str | None:
    return ph_holidays(d.year).get(d)


def get_mode(s: Session, d: date) -> str:
    row = s.get(DayMode, d)
    if row:
        return row.mode
    if holiday_name(d):
        return "holiday"
    return "work" if d.weekday() < 5 else "off"


def is_active(s: Session, d: date) -> bool:
    return get_mode(s, d) in ACTIVE


def set_mode(s: Session, d: date, mode: str, source: str = "user") -> None:
    row = s.get(DayMode, d)
    if row:
        row.mode, row.source = mode, source
    else:
        s.add(DayMode(date=d, mode=mode, source=source))


def context_for(s: Session, d: date) -> str:
    return "school" if get_mode(s, d) == "aral" else "work"
