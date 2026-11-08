from sqlalchemy import select

from app.config import Settings
from app.db.models import Meeting, Outbox
from conftest import dt


def ics(*events: str) -> str:
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//\r\n" + "".join(events) + "END:VCALENDAR\r\n"


def ev(uid, summary, start, end, extra=""):
    return (f"BEGIN:VEVENT\r\nUID:{uid}\r\nSUMMARY:{summary}\r\nDTSTART:{start}\r\nDTEND:{end}\r\n{extra}"
            "END:VEVENT\r\n")


def cal_harness(make, start, feed):
    cfg = Settings(_env_file=None, parser="rule", owner_psid="u1", calendar_ics_urls="http://x/cal.ics",
                   calendar_sync_minutes=15)
    h = make(start, settings=cfg)
    h.state = {"feed": feed}
    h.bot.calendar_fetch = lambda: [h.state["feed"]]
    return h


def meetings(h, **kw):
    with h.sessions() as s:
        return list(s.scalars(select(Meeting).order_by(Meeting.start_at)))


def meet_rows(h, status="scheduled"):
    with h.sessions() as s:
        return list(s.scalars(select(Outbox).where(Outbox.kind == "meeting", Outbox.status == status)
                              .order_by(Outbox.due_at)))


# UTC times: 2026-11-10 02:00Z = 10:00 Manila (UTC+8)
FEED = ics(ev("a1", "Budget meeting", "20261110T020000Z", "20261110T030000Z"))


def test_ics_event_becomes_meeting_with_30_and_10_min_reminders(make):
    h = cal_harness(make, dt(11, 9, 17, 0), FEED)
    h.advance(minutes=1)
    ms = meetings(h)
    assert [(m.title, m.source) for m in ms] == [("Budget meeting", "ics")]
    assert ms[0].start_at == dt(11, 10, 10, 0)  # converted to Manila
    due = [r.due_at for r in meet_rows(h)]
    assert due == [dt(11, 10, 9, 30), dt(11, 10, 9, 50)]


def test_reminder_is_actually_delivered(make):
    h = cal_harness(make, dt(11, 9, 17, 0), FEED)
    h.say("hi")  # open the window
    h.advance_to(dt(11, 10, 9, 31))
    assert any("30 min na lang: Budget meeting" in t for t in h.texts())


def test_sync_is_idempotent(make):
    h = cal_harness(make, dt(11, 9, 17, 0), FEED)
    h.advance(minutes=1)
    h.advance(minutes=20)
    h.advance(minutes=20)
    assert len(meetings(h)) == 1
    assert len(meet_rows(h)) == 2


def test_removed_from_feed_cancels_meeting_and_reminders(make):
    h = cal_harness(make, dt(11, 9, 17, 0), FEED)
    h.advance(minutes=1)
    h.state["feed"] = ics()
    h.advance(minutes=20)
    assert [m.status for m in meetings(h)] == ["cancelled"]
    assert meet_rows(h) == []


def test_retitled_event_updates_title_in_reminders(make):
    h = cal_harness(make, dt(11, 9, 17, 0), FEED)
    h.advance(minutes=1)
    h.state["feed"] = ics(ev("a1", "Budget meeting (moved to Zoom)", "20261110T020000Z", "20261110T030000Z"))
    h.advance(minutes=20)
    assert len(meetings(h)) == 1
    assert all("Zoom" in r.text for r in meet_rows(h))


def test_rescheduled_event_replaces_old_one(make):
    h = cal_harness(make, dt(11, 9, 17, 0), FEED)
    h.advance(minutes=1)
    h.state["feed"] = ics(ev("a1", "Budget meeting", "20261110T060000Z", "20261110T070000Z"))  # now 14:00
    h.advance(minutes=20)
    active = [m for m in meetings(h) if m.status == "active"]
    assert [m.start_at for m in active] == [dt(11, 10, 14, 0)]
    assert [r.due_at for r in meet_rows(h)] == [dt(11, 10, 13, 30), dt(11, 10, 13, 50)]


def test_recurring_event_expands(make):
    daily = ev("r1", "Standup", "20261110T010000Z", "20261110T011500Z", "RRULE:FREQ=DAILY;COUNT=3\r\n")
    h = cal_harness(make, dt(11, 9, 17, 0), ics(daily))
    h.advance(minutes=1)
    assert [m.start_at for m in meetings(h)] == [dt(11, 10, 9, 0), dt(11, 11, 9, 0), dt(11, 12, 9, 0)]


def test_all_day_and_cancelled_events_are_skipped(make):
    allday = "BEGIN:VEVENT\r\nUID:d1\r\nSUMMARY:Holiday\r\nDTSTART;VALUE=DATE:20261110\r\nEND:VEVENT\r\n"
    cancelled = ev("c1", "Dropped", "20261110T040000Z", "20261110T050000Z", "STATUS:CANCELLED\r\n")
    h = cal_harness(make, dt(11, 9, 17, 0), ics(allday, cancelled))
    h.advance(minutes=1)
    assert meetings(h) == []


def test_chat_meetings_are_not_touched_and_cancelled_ics_not_resurrected(make):
    h = cal_harness(make, dt(11, 9, 12, 0), ics())  # before the 16:30 check-in
    h.say("meeting bukas 3pm with finance")
    h.tap("CONFIRM")
    h.advance(minutes=20)
    chat = [m for m in meetings(h) if m.source == "chat"]
    assert len(chat) == 1 and chat[0].status == "active"
    # user cancels an ics meeting in chat; the next sync must not bring it back
    h.state["feed"] = FEED
    h.advance(minutes=20)
    h.say("cancel budget meeting")
    h.advance(minutes=20)
    assert [m.status for m in meetings(h) if m.source == "ics"] == ["cancelled"]
    assert meet_rows(h) == [] or all("Budget" not in r.text for r in meet_rows(h))


def test_feed_failure_keeps_existing_meetings(make):
    h = cal_harness(make, dt(11, 9, 17, 0), FEED)
    h.advance(minutes=1)
    h.bot.calendar_fetch = lambda: []  # download failed
    h.advance(minutes=20)
    assert [m.status for m in meetings(h)] == ["active"]


def test_bad_ics_does_not_cancel_everything(make):
    h = cal_harness(make, dt(11, 9, 17, 0), FEED)
    h.advance(minutes=1)
    h.state["feed"] = "this is not a calendar"
    h.advance(minutes=20)
    assert [m.status for m in meetings(h)] == ["active"]


def test_sync_disabled_when_no_url(make):
    h = make(dt(11, 9, 17, 0))
    called = []
    h.bot.calendar_fetch = lambda: called.append(1) or []
    h.advance(minutes=30)
    assert called == []
