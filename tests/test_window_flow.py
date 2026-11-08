"""Window warning rescheduling, the off-weekend timeline, and the pending queue."""
from sqlalchemy import select

from app.db.models import Outbox
from conftest import dt


def warning_row(h):
    with h.sessions() as s:
        return s.scalar(select(Outbox).where(Outbox.dedup_key == "warning"))


def warnings(h):
    return h.sent_at("Magsasara na ang chat window")


def test_warning_is_one_row_replaced_on_every_inbound(make):
    h = make(dt(11, 10, 9, 0))
    h.say("hi")
    assert warning_row(h).due_at == dt(11, 11, 6, 0)  # 09:00 + 21h = 06:00 (not quiet: 06:00 is allowed)
    h.advance(hours=2)
    h.say("hello")
    assert warning_row(h).due_at == dt(11, 11, 8, 0)
    with h.sessions() as s:
        assert len(list(s.scalars(select(Outbox).where(Outbox.kind == "warning")))) == 1


def test_warning_fires_at_21h_with_quick_reply_and_mentions_upcoming(make):
    h = make(dt(11, 10, 10, 0))
    h.say("paalala mo ko bukas 9am tumawag sa vendor")
    h.advance_to(dt(11, 11, 7, 1))
    m = [m for m in h.channel.sent if "Magsasara na" in m["text"]]
    assert m and m[0]["at"] == dt(11, 11, 7, 0)
    assert m[0]["quick_replies"] == [{"title": "👍 Tuloy", "payload": "KEEPALIVE"}]
    assert "tumawag sa vendor" in m[0]["text"]


def test_tapping_keepalive_reopens_window_and_reschedules(make):
    h = make(dt(11, 10, 10, 0))
    h.say("hi")
    h.advance_to(dt(11, 11, 7, 0))
    h.tap("KEEPALIVE", "👍 Tuloy")
    assert warning_row(h).due_at == dt(11, 12, 4, 0).replace(hour=22, day=11)  # 28:00 -> quiet -> 22:00 earlier


def test_reaction_counts_as_inbound(make):
    h = make(dt(11, 10, 10, 0))
    h.say("hi")
    h.advance(hours=20)
    h.react()
    assert warning_row(h).due_at > dt(11, 11, 6, 0)
    h.advance(hours=12)  # 32h after first message: window only open because of the reaction
    assert h.bot.window_ok(h.sessions(), h.clock.now())


def test_nudges_do_not_extend_window(make):
    h = make(dt(11, 10, 6, 55))
    h.say("hi")
    h.advance_to(dt(11, 10, 12, 0))  # briefing + hourly nudges were sent
    with h.sessions() as s:
        assert h.bot._state(s).last_inbound_at == dt(11, 10, 6, 55)


def test_closed_window_blocks_sending(make):
    h = make(dt(11, 10, 6, 0))
    h.say("hi")
    h.advance_to(dt(11, 11, 6, 0))  # 24h later, nobody replied
    h.channel.sent.clear()
    h.advance_to(dt(11, 11, 8, 0))
    assert h.texts() == []  # briefing stays pending - nothing leaks outside the window
    with h.sessions() as s:
        pend = list(s.scalars(select(Outbox).where(Outbox.status == "pending")))
    assert any(p.kind == "briefing" for p in pend)


# -------------------------------------------------------------- off weekend timeline

def _friday_checkin_and_off(h):
    h.say("hi")
    h.advance_to(dt(11, 13, 16, 30))
    h.say("wala")
    h.tap("MODE:2026-11-14:off")
    h.tap("MODE:2026-11-15:off")


def test_off_weekend_timeline(make):
    """Fri 16:30 answer -> Sat 13:30 warning -> tap -> Sun 10:30 warning -> tap -> Mon 07:30 briefing."""
    h = make(dt(11, 13, 16, 20))
    _friday_checkin_and_off(h)
    h.advance_to(dt(11, 14, 13, 29))
    assert warnings(h) == []
    h.advance_to(dt(11, 14, 13, 31))
    assert warnings(h) == [dt(11, 14, 13, 30)]
    h.tap("KEEPALIVE")  # taps at 13:31 -> next warning 10:31 Sunday
    h.advance_to(dt(11, 15, 10, 32))
    assert warnings(h)[-1] == dt(11, 15, 10, 31)
    h.tap("KEEPALIVE")
    h.advance_to(dt(11, 16, 7, 31))
    assert [t for t in h.sent_at("Magandang umaga") if t >= dt(11, 14, 0, 0)] == [dt(11, 16, 7, 30)]
    # nothing else (no briefing / check-in / nudges) on the off days
    between = [m for m in h.channel.sent if dt(11, 14, 0, 0) <= m["at"] < dt(11, 16, 7, 30)]
    assert all("Magsasara na ang chat window" in m["text"] or "Sige, tuloy" in m["text"] for m in between)


def test_off_weekend_missed_tap_goes_to_pending_and_late_briefing(make):
    h = make(dt(11, 13, 16, 20))
    _friday_checkin_and_off(h)
    h.advance_to(dt(11, 14, 13, 31))  # got the warning, did NOT tap
    h.advance_to(dt(11, 16, 7, 45))
    assert [t for t in h.sent_at("Magandang umaga") if t >= dt(11, 14, 0, 0)] == []  # window closed -> pending, not sent
    out = h.say("good morning")
    assert any("Magandang umaga" in t for t in out)  # late briefing on first inbound


def test_pending_queue_delivers_na_miss_mo_first(make):
    h = make(dt(11, 13, 10, 0))
    h.say("hi")
    h.say("paalala mo ko ngayong Sabado 10am i-backup ang server")
    if "Tama ba" in h.last():
        h.tap("CONFIRM")
    h.say("may meeting sa Linggo 9am with the director")
    if "Tama ba" in h.last():
        h.tap("CONFIRM")
    h.advance_to(dt(11, 14, 10, 30))  # Saturday: window closed since Fri 10:00 + 23h
    assert not any("backup" in t and "Paalala" in t for t in h.texts())
    out = h.say("hello")
    assert out[0].startswith("Na-miss mo:")
    assert "backup" in out[0]


def test_pending_not_delivered_twice(make):
    h = make(dt(11, 13, 10, 0))
    h.say("hi")
    h.say("paalala mo ko ngayong Sabado 10am i-backup ang server")
    if "Tama ba" in h.last():
        h.tap("CONFIRM")
    h.advance_to(dt(11, 14, 10, 30))
    h.say("hello")
    n = sum("Na-miss mo:" in t for t in h.texts())
    h.say("hello ulit")
    assert sum("Na-miss mo:" in t for t in h.texts()) == n == 1


def test_missed_reminder_with_open_window_is_marked_missed(make):
    h = make(dt(11, 10, 8, 0))
    h.say("hi")
    h.say("paalala mo ko ngayon 9am tumawag")
    h.clock.set(dt(11, 10, 14, 0))  # bot was off through the reminder and its grace period
    h.bot.tick()
    assert any("Na-miss mo" in t and "tumawag" in t for t in h.texts())
