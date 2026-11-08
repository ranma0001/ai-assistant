"""Resolve Taglish/English time expressions to datetimes in Asia/Manila.

The LLM only extracts the expression as text; ALL date/time math happens here.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.timeutil import TZ, ensure_aware

MONTHS = {
    "jan": 1, "january": 1, "enero": 1, "feb": 2, "february": 2, "pebrero": 2,
    "mar": 3, "march": 3, "marso": 3, "apr": 4, "april": 4, "abril": 4,
    "may": 5, "mayo": 5, "jun": 6, "june": 6, "hunyo": 6, "jul": 7, "july": 7, "hulyo": 7,
    "aug": 8, "august": 8, "agosto": 8, "sep": 9, "sept": 9, "september": 9,
    "setyembre": 9, "septyembre": 9, "oct": 10, "october": 10, "oktubre": 10,
    "nov": 11, "november": 11, "nobyembre": 11, "dec": 12, "december": 12, "disyembre": 12,
}
WEEKDAYS = {
    "lunes": 0, "monday": 0, "mon": 0, "martes": 1, "tuesday": 1, "tue": 1, "tues": 1,
    "miyerkules": 2, "miyerkoles": 2, "wednesday": 2, "wed": 2,
    "huwebes": 3, "thursday": 3, "thu": 3, "thurs": 3,
    "biyernes": 4, "friday": 4, "fri": 4, "sabado": 5, "saturday": 5, "sat": 5,
    "linggo": 6, "domingo": 6, "sunday": 6, "sun": 6,
}
PERIOD_DEFAULT_HOUR = {"umaga": 8, "tanghali": 12, "hapon": 15, "gabi": 19, "madaling araw": 5}
PERIOD_ALIASES = {
    "umaga": "umaga", "morning": "umaga", "tanghali": "tanghali", "noon": "tanghali",
    "hapon": "hapon", "afternoon": "hapon", "gabi": "gabi", "evening": "gabi", "night": "gabi",
    "tonight": "gabi",
}
NUMWORDS = {"isa": 1, "isang": 1, "one": 1, "dalawa": 2, "dalawang": 2, "two": 2,
            "tatlo": 3, "tatlong": 3, "three": 3, "apat": 4, "limang": 5, "lima": 5}


@dataclass
class Resolved:
    dt: datetime | None
    granularity: str = "minute"  # minute | day | month | week
    ambiguous: bool = False
    note: str = ""

    @property
    def date_only(self) -> bool:
        return self.granularity in ("day", "month", "week")


def _norm(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[,.;!?]+", " ", t)
    t = re.sub(r"\balas[\s-]*", "alas ", t)
    t = re.sub(r"\b(ngayong|ngayon)\b", r"\1", t)
    t = re.sub(r"\s+", " ", t)
    return t


def _next_weekday(today: date, wd: int, *, strict_future: bool, skip_week: bool = False) -> date:
    delta = (wd - today.weekday()) % 7
    if delta == 0 and strict_future:
        delta = 7
    d = today + timedelta(days=delta)
    if skip_week:
        d += timedelta(days=7)
    return d


def _find_period(t: str) -> str | None:
    if "madaling araw" in t:
        return "madaling araw"
    for w in re.findall(r"[a-z]+", t):
        if w in PERIOD_ALIASES:
            return PERIOD_ALIASES[w]
    return None


def _find_time(t: str, period: str | None):
    """Return (hour, minute, guessed) or None. Dates must be removed from t first."""
    m = re.search(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b", t)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3)
        if h >= 13 or h == 0:
            return h % 24, mi, False
        if ap:
            return _h24(h, ap), mi, False
        return _guess_hour(h, period), mi, True
    m = re.search(r"\b(\d{1,2})\s*(am|pm|a\.m\.|p\.m\.)", t)
    if m:
        return _h24(int(m.group(1)), m.group(2)[0] + "m"), 0, False
    m = re.search(r"\balas (\d{1,2})\b", t)
    if m:
        return _guess_hour(int(m.group(1)), period), 0, period is None
    if period:
        m = re.search(r"\b(\d{1,2})\b(?=\s*(?:ng\s+)?(?:umaga|tanghali|hapon|gabi|madaling))", t)
        if m:
            return _guess_hour(int(m.group(1)), period), 0, False
    m = re.search(r"^[^\d]*(\d{1,2})\s*$", t)
    if m:
        return _guess_hour(int(m.group(1)), period), 0, True
    return None


def _h24(h: int, ap: str) -> int:
    if ap == "am":
        return 0 if h == 12 else h
    return 12 if h == 12 else h + 12


def _guess_hour(h: int, period: str | None) -> int:
    if h >= 13 or h == 0:
        return h % 24
    if period in ("hapon", "gabi"):
        return 12 if h == 12 else h + 12 if h < 12 else h
    if period == "umaga" or period == "madaling araw":
        return 0 if h == 12 else h
    if period == "tanghali":
        return 12 if h in (11, 12) else h + 12 if h < 6 else h
    # no period: office hours heuristic
    if h == 12:
        return 12
    return h + 12 if h <= 6 else h


def _fix_year(month: int, day: int | None, today: date) -> int:
    year = today.year
    if day is not None:
        try:
            if date(year, month, day) < today - timedelta(days=1):
                year += 1
        except ValueError:
            pass
    elif month < today.month:
        year += 1
    return year


def parse_when(text: str, now: datetime, default_hour: int = 9) -> Resolved | None:
    now = ensure_aware(now)
    today = now.date()
    t = _norm(text)
    if not t:
        return None

    # relative durations ("sa loob ng 2 oras", "in 30 minutes", "1 oras")
    m = re.search(
        r"(?:sa loob ng |in |after |makalipas ang |sa )?\b(\d+|kalahating|"
        + "|".join(NUMWORDS) + r")\s*(oras|hr|hrs|hour|hours|minuto|min|mins|minutes|araw|day|days)\b", t)
    if m and (re.match(r"(sa loob|in |after|makalipas)", m.group(0)) or t == m.group(0).strip()
              or re.fullmatch(r"(\d+|" + "|".join(NUMWORDS) + r")\s*\w+", t)):
        raw = m.group(1)
        n = NUMWORDS.get(raw, None) if not raw.isdigit() else int(raw)
        unit = m.group(2)
        if raw == "kalahating":
            return Resolved(now + timedelta(minutes=30), "minute")
        if n is not None:
            if unit.startswith(("oras", "hr", "hour")):
                return Resolved(now + timedelta(hours=n), "minute")
            if unit.startswith(("min",)):
                return Resolved(now + timedelta(minutes=n), "minute")
            return Resolved(now + timedelta(days=n), "day", ambiguous=True)

    # weeks
    if re.search(r"\b(susunod na linggo|next week|sa susunod na linggo)\b", t):
        mon = today + timedelta(days=7 - today.weekday())
        return Resolved(_dt(mon, default_hour), "week", True, "next week -> Monday")
    if re.search(r"\b(ngayong linggo|this week)\b", t):
        fri = today + timedelta(days=max(0, 4 - today.weekday()))
        return Resolved(_dt(fri, 17), "week", True, "this week -> Friday")

    # explicit dates
    date_span = None
    d: date | None = None
    granularity = "minute"
    mm = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t)
    if mm:
        mo, dd = int(mm.group(1)), int(mm.group(2))
        y = int(mm.group(3)) if mm.group(3) else None
        if y and y < 100:
            y += 2000
        try:
            d = date(y or _fix_year(mo, dd, today), mo, dd)
            date_span = mm.span()
        except ValueError:
            return None
    if d is None:
        mname = "|".join(sorted(MONTHS, key=len, reverse=True))
        m1 = re.search(rf"\b({mname})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", t)
        m2 = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:ng\s+)?({mname})\b", t)
        m3 = re.search(rf"\b({mname})\b", t)
        mo = dd = None
        if m1:
            mo, dd, date_span = MONTHS[m1.group(1)], int(m1.group(2)), m1.span()
        elif m2:
            mo, dd, date_span = MONTHS[m2.group(2)], int(m2.group(1)), m2.span()
        elif m3 and not (m3.group(1) in ("may", "mar", "sat", "sun") and not _month_context(t)):
            mo = MONTHS[m3.group(1)]
            y = _fix_year(mo, None, today)
            last = calendar.monthrange(y, mo)[1]
            return Resolved(_dt(date(y, mo, last), 17), "month", False, "end of month")
        if mo is not None:
            try:
                d = date(_fix_year(mo, dd, today), mo, dd)
            except ValueError:
                return None

    rest = t if date_span is None else t[: date_span[0]] + " " + t[date_span[1]:]
    period = _find_period(rest)
    ambiguous = False
    note = ""

    if d is None:
        words = re.findall(r"[a-zñ]+", rest)
        wset = set(words)
        if "bukas" in wset or "tomorrow" in wset:
            d = today + timedelta(days=1)
            if "bukas ng" in rest and "gabi" in rest:
                pass
        elif "makalawa" in wset:
            d = today + timedelta(days=2)
        elif wset & {"ngayon", "ngayong", "today", "tonight", "mamaya", "mamayang"} and not (
                {"ngayong"} & wset and wset & set(WEEKDAYS)):
            d = today
        else:
            wd = next((WEEKDAYS[w] for w in words if w in WEEKDAYS and not (w == "linggo" and "ngayong" in wset and False)), None)
            if wd is not None:
                skip = bool(re.search(r"\b(susunod na|next)\b", rest))
                ngayong = "ngayong" in wset or "this" in wset
                d = _next_weekday(today, wd, strict_future=False, skip_week=skip)
                if d == today and not ngayong:
                    # "sa Huwebes" said on a Thursday: today if time is still ahead, else next week
                    tm0 = _find_time(rest, period)
                    if tm0 is None or _dt(d, tm0[0], tm0[1]) <= now:
                        d += timedelta(days=7)
    if d is None:
        # last resort: dateparser (English phrases) - flagged ambiguous
        return _dateparser_fallback(text, now)

    tm = _find_time(rest, period)
    if tm:
        h, mi, guessed = tm
        res = Resolved(_dt(d, h, mi), "minute", guessed, "guessed am/pm" if guessed else "")
        if guessed:
            res.note = "Hindi sigurado kung AM o PM"
    elif period:
        res = Resolved(_dt(d, PERIOD_DEFAULT_HOUR[period]), "minute", True, f"assumed {period}")
    elif re.search(r"\bmamaya\b", rest) and d == today:
        dt = (now + timedelta(hours=2)).replace(second=0, microsecond=0)
        dt = dt.replace(minute=(dt.minute // 15) * 15)
        res = Resolved(dt, "minute", True, "mamaya -> +2h")
    else:
        res = Resolved(_dt(d, default_hour), "day", True, "walang oras; default")
    res.ambiguous = res.ambiguous or ambiguous
    return res


def _month_context(t: str) -> bool:
    """'may' / 'mar' are also normal words; only treat as month with a month cue."""
    return bool(re.search(r"\b(ngayong|sa|ng|this|next|buwan|of)\s+(may|mar)\b|\b(may|mar)\s+\d{1,2}\b", t))


def _dt(d: date, h: int, mi: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h % 24, mi, tzinfo=TZ)


def _dateparser_fallback(text: str, now: datetime) -> Resolved | None:
    try:
        import dateparser

        dt = dateparser.parse(text, settings={
            "TIMEZONE": "Asia/Manila", "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future", "RELATIVE_BASE": now.replace(tzinfo=None),
        })
    except Exception:  # noqa: BLE001
        return None
    if not dt:
        return None
    return Resolved(ensure_aware(dt), "minute", True, "dateparser fallback")


# ---------------------------------------------------------------- phrase finder

_CONNECT = {"sa", "ng", "na", "ngayong", "ngayon", "this", "next", "susunod", "at", "on", "by", "ang", "alas"}
_BASE = {"bukas", "mamaya", "mamayang", "ngayon", "ngayong", "today", "tomorrow", "tonight", "makalawa",
         "umaga", "tanghali", "hapon", "gabi", "morning", "afternoon", "evening", "noon", "night",
         "alas", "linggo", "oras", "minuto"} | set(WEEKDAYS) | set(MONTHS)


def find_when_span(text: str) -> tuple[int, int] | None:
    """Locate the time expression inside a message: returns (start, end) char offsets."""
    toks = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    flags = []
    for i, (w, _, _) in enumerate(toks):
        lw = re.sub(r"[,.;!?]+$", "", w.lower())
        hit = (
            lw in _BASE
            or bool(re.fullmatch(r"\d{1,2}(:\d{2})?\s*(am|pm)", lw))
            or bool(re.fullmatch(r"\d{1,2}:\d{2}", lw))
            or bool(re.fullmatch(r"\d{1,2}/\d{1,2}(/\d{2,4})?", lw))
            or bool(re.fullmatch(r"alas-\d{1,2}", lw))
        )
        if lw in ("may", "mar", "sat", "sun", "mon", "wed", "sept", "jun", "jul", "dec") and lw not in ("sun",):
            hit = False
        if lw.isdigit() and i > 0:
            prev = re.sub(r"[,.;!?]+$", "", toks[i - 1][0].lower())
            nxt = re.sub(r"[,.;!?]+$", "", toks[i + 1][0].lower()) if i + 1 < len(toks) else ""
            hit = prev in ("alas", "next") or prev in MONTHS or nxt in MONTHS or nxt in (
                "oras", "minuto", "ng", "hours", "hour", "mins", "minutes", "am", "pm") or \
                prev in ("loob", "in", "after", "ang")
        flags.append(hit)
    best = None
    i = 0
    while i < len(toks):
        if not flags[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(toks):
            lw = re.sub(r"[,.;!?]+$", "", toks[j + 1][0].lower())
            if flags[j + 1] or (lw in _CONNECT and j + 2 < len(toks) and flags[j + 2]):
                j += 1
            else:
                break
        # include leading connectors
        s = i
        while s > 0 and re.sub(r"[,.;!?]+$", "", toks[s - 1][0].lower()) in ("sa", "ngayong", "next", "susunod", "na", "on", "by", "ng") and s > 0:
            s -= 1
        end = toks[j][2]
        text_end = re.sub(r"[,.;!?]+$", "", text[toks[s][1]:end])
        span = (toks[s][1], toks[s][1] + len(text_end))
        if best is None or (span[1] - span[0]) > (best[1] - best[0]):
            best = span
        i = j + 1
    return best


# ---------------------------------------------------------------- report ranges

def resolve_range(text: str | None, today: date) -> tuple[date, date, str]:
    t = _norm(text or "")
    mname = "|".join(sorted(MONTHS, key=len, reverse=True))
    m = re.search(rf"\b({mname})\b", t)
    if m and not (m.group(1) in ("may", "mar") and not _month_context(t) and len(t.split()) > 1 and "buwan" not in t):
        mo = MONTHS[m.group(1)]
        y = today.year if mo <= today.month else today.year - 1
        last = calendar.monthrange(y, mo)[1]
        return date(y, mo, 1), date(y, mo, last), f"{calendar.month_name[mo]} {y}"
    if re.search(r"\b(last month|nakaraang buwan|nakaraang buwan)\b", t):
        first = today.replace(day=1) - timedelta(days=1)
        return first.replace(day=1), first, f"{calendar.month_name[first.month]} {first.year}"
    if re.search(r"\b(last week|nakaraang linggo)\b", t):
        mon = today - timedelta(days=today.weekday() + 7)
        return mon, mon + timedelta(days=6), "Last week"
    if re.search(r"\b(this week|ngayong linggo|weekly|week)\b", t):
        mon = today - timedelta(days=today.weekday())
        return mon, mon + timedelta(days=6), "This week"
    last = calendar.monthrange(today.year, today.month)[1]
    return today.replace(day=1), today.replace(day=last), f"{calendar.month_name[today.month]} {today.year}"
