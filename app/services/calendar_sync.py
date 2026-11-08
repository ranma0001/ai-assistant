"""Read-only calendar sync from iCal/ICS feed URLs (Google "secret address in iCal format", Outlook "published
calendar" ICS, etc.). No OAuth, nothing is written back to the calendar.

`parse_events` is pure (ICS text -> events); `fetch_ics` does the HTTP. The Assistant upserts the events as
Meeting rows (source="ics") and schedules the 30/10-min reminders the same way as chat meetings.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx

from app.timeutil import TZ

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CalEvent:
    external_id: str  # UID + start, so each occurrence of a recurring event is distinct
    title: str
    start: datetime
    end: datetime | None
    attendees: tuple[str, ...] = ()


def fetch_ics(urls: list[str], timeout: float = 20.0) -> list[str]:
    """Download each feed. A failing feed is logged and skipped (never raises)."""
    out: list[str] = []
    for u in urls:
        try:
            r = httpx.get(u, timeout=timeout, follow_redirects=True)
            r.raise_for_status()
            out.append(r.text)
        except Exception as e:  # noqa: BLE001
            log.warning("calendar fetch failed (%s): %s", u.split("?")[0], e)
    return out


def _to_manila(v: date | datetime) -> datetime | None:
    if not isinstance(v, datetime):  # all-day event: no meeting time -> not a meeting reminder
        return None
    if v.tzinfo is None:  # floating time: assume Manila
        return v.replace(tzinfo=TZ)
    return v.astimezone(TZ)


def parse_events(ics_text: str, start: datetime, end: datetime) -> list[CalEvent]:
    """Events (incl. expanded recurrences) starting in [start, end). Skips all-day and cancelled ones."""
    import icalendar
    import recurring_ical_events

    cal = icalendar.Calendar.from_ical(ics_text)
    out: list[CalEvent] = []
    for ev in recurring_ical_events.of(cal).between(start, end):
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        st = _to_manila(ev.decoded("DTSTART"))
        if st is None or not (start <= st < end):
            continue
        en = _to_manila(ev.decoded("DTEND")) if ev.get("DTEND") else None
        att = ev.get("ATTENDEE")
        att = att if isinstance(att, list) else ([att] if att else [])
        names = tuple(sorted({(a.params.get("CN") or str(a).replace("mailto:", "")) for a in att}))
        uid = str(ev.get("UID", "")) or str(ev.get("SUMMARY", ""))
        out.append(CalEvent(f"{uid}@{st:%Y%m%dT%H%M}", str(ev.get("SUMMARY", "")).strip() or "(walang pamagat)",
                            st, en, names))
    out.sort(key=lambda e: e.start)
    return out


def window(now: datetime, days: int) -> tuple[datetime, datetime]:
    return now, now + timedelta(days=days)
