from datetime import datetime, timedelta

from app.timeutil import TZ, shift_quiet, window_open
from conftest import dt


def test_window_open_boundaries():
    last = dt(11, 9, 8, 0)
    assert window_open(last, last + timedelta(hours=22, minutes=59))
    assert not window_open(last, last + timedelta(hours=23, minutes=1))
    assert not window_open(None, last)


def test_quiet_shift_is_always_earlier():
    assert shift_quiet(dt(11, 9, 23, 30)) == dt(11, 9, 22, 0)
    assert shift_quiet(dt(11, 9, 23, 0)) == dt(11, 9, 22, 0)
    assert shift_quiet(dt(11, 10, 5, 0)) == dt(11, 9, 22, 0)
    assert shift_quiet(dt(11, 10, 0, 0)) == dt(11, 9, 22, 0)
    assert shift_quiet(dt(11, 10, 6, 0)) == dt(11, 10, 6, 0)
    assert shift_quiet(dt(11, 9, 22, 59)) == dt(11, 9, 22, 59)
    for h in range(24):
        for m in (0, 30):
            x = dt(11, 9, h, m)
            assert shift_quiet(x) <= x


def test_reminder_in_quiet_hours_goes_out_earlier(make):
    h = make(dt(11, 9, 20, 0))
    h.say("hello")
    h.say("paalala mo ko bukas 5am magpasa ng report")
    assert "5:00" in h.last() or "Tama ba" in h.last()
    h.tap("CONFIRM") if "Tama ba" in h.last() else None
    h.advance_to(dt(11, 9, 22, 30))
    sent = [m for m in h.channel.sent if "Paalala" in m["text"]]
    assert sent, h.texts()
    assert sent[0]["at"] == dt(11, 9, 22, 0)
    assert "para sa" in sent[0]["text"]


def test_warning_in_quiet_hours_is_moved_earlier(make):
    h = make(dt(11, 9, 2, 0))  # Monday 02:00 -> +21h = 23:00 -> must go out at 22:00
    h.say("hello")
    w = h.sessions().execute(__import__("sqlalchemy").text(
        "select due_at from outbox where dedup_key='warning'")).scalar()
    assert str(w).startswith("2026-11-09 22:00")
