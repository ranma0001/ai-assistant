from datetime import date, datetime

import pytest

from app.nlp.when_parser import find_when_span, parse_when, resolve_range
from app.timeutil import TZ

NOW = datetime(2026, 11, 8, 10, 0, tzinfo=TZ)  # Sunday


def dt(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=TZ)


CASES = [
    ("bukas 3pm", dt(2026, 11, 9, 15), False),
    ("bukas ng 3pm", dt(2026, 11, 9, 15), False),
    ("bukas 3:30pm", dt(2026, 11, 9, 15, 30), False),
    ("bukas alas-3 ng hapon", dt(2026, 11, 9, 15), False),
    ("bukas alas 10 ng umaga", dt(2026, 11, 9, 10), False),
    ("sa Huwebes 10am", dt(2026, 11, 12, 10), False),
    ("sa Lunes 9am", dt(2026, 11, 9, 9), False),
    ("ngayong Sabado", dt(2026, 11, 14, 9), True),
    ("susunod na Lunes 8am", dt(2026, 11, 16, 8), False),
    ("bukas umaga", dt(2026, 11, 9, 8), True),
    ("bukas hapon", dt(2026, 11, 9, 15), True),
    ("bukas gabi", dt(2026, 11, 9, 19), True),
    ("bukas tanghali", dt(2026, 11, 9, 12), True),
    ("mamaya 5pm", dt(2026, 11, 8, 17), False),
    ("mamaya", dt(2026, 11, 8, 12), True),
    ("ngayon 4pm", dt(2026, 11, 8, 16), False),
    ("bukas 15:00", dt(2026, 11, 9, 15), False),
    ("bukas 3", dt(2026, 11, 9, 15), True),
    ("sa loob ng 2 oras", dt(2026, 11, 8, 12), False),
    ("30 minuto", dt(2026, 11, 8, 10, 30), False),
    ("nov 20 2pm", dt(2026, 11, 20, 14), False),
    ("11/20 9am", dt(2026, 11, 20, 9), False),
    ("makalawa 2pm", dt(2026, 11, 10, 14), False),
]


@pytest.mark.parametrize("text,expected,ambiguous", CASES)
def test_cases(text, expected, ambiguous):
    r = parse_when(text, NOW)
    assert r is not None, text
    assert r.dt == expected, (text, r)
    assert r.ambiguous == ambiguous, (text, r)


def test_month_is_end_of_month():
    r = parse_when("ngayong Nobyembre", NOW)
    assert r.granularity == "month" and r.dt.date() == date(2026, 11, 30)
    r = parse_when("Disyembre", NOW)
    assert r.dt.date() == date(2026, 12, 31)


def test_past_month_rolls_to_next_year():
    r = parse_when("Enero", NOW)
    assert r.dt.date() == date(2027, 1, 31)


def test_same_weekday_time_passed_goes_next_week():
    thursday = datetime(2026, 11, 12, 11, 0, tzinfo=TZ)
    assert parse_when("sa Huwebes 10am", thursday).dt == dt(2026, 11, 19, 10)
    assert parse_when("sa Huwebes 3pm", thursday).dt == dt(2026, 11, 12, 15)


def test_garbage_returns_none():
    assert parse_when("kumain na tayo", NOW) is None


def test_always_aware_manila():
    r = parse_when("bukas 3pm", NOW)
    assert r.dt.tzinfo is not None and r.dt.utcoffset().total_seconds() == 8 * 3600


@pytest.mark.parametrize("msg,expect", [
    ("paalala mo ko bukas 3pm magpasa ng report", "bukas 3pm"),
    ("may meeting sa Huwebes 10am with the director", "sa Huwebes 10am"),
    ("mag-aaral ako ngayong Sabado", "ngayong Sabado"),
    ("gawa AR ngayong Nobyembre", "ngayong Nobyembre"),
    ("naka-leave ako bukas", "bukas"),
    ("remind me bukas alas-3 ng hapon to call IT", "bukas alas-3 ng hapon"),
])
def test_find_span(msg, expect):
    s = find_when_span(msg)
    assert s is not None
    assert msg[s[0]:s[1]] == expect


def test_no_span():
    assert find_when_span("tapos ko na yung login bug") is None


def test_ranges():
    today = date(2026, 11, 8)
    assert resolve_range("Oktubre", today)[:2] == (date(2026, 10, 1), date(2026, 10, 31))
    assert resolve_range("this month", today)[:2] == (date(2026, 11, 1), date(2026, 11, 30))
    assert resolve_range("last week", today)[:2] == (date(2026, 10, 26), date(2026, 11, 1))
