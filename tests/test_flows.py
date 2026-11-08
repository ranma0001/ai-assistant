from sqlalchemy import select

from app.db.models import DailyRun, DayMode, LogEntry, Meeting, Outbox, Reminder, Task
from conftest import dt


def rows(h, model, *where):
    with h.sessions() as s:
        return list(s.scalars(select(model).where(*where)))


# ------------------------------------------------------------------ Phase 1: log + check-in

def test_log_requires_confirmation_then_saves(make):
    h = make(dt(11, 10, 10, 0))
    out = h.say("tapos ko na yung login bug, tsaka nag-assist ako sa printer sa admin")
    assert "Ito ang naintindihan ko" in out[-1]
    assert rows(h, LogEntry) == []  # nothing saved before confirmation
    h.tap("CONFIRM")
    entries = rows(h, LogEntry)
    assert [e.raw_text for e in entries] == ["tapos ko na yung login bug", "nag-assist ako sa printer sa admin"]
    assert all(e.context == "work" for e in entries)


def test_log_cancel_saves_nothing(make):
    h = make(dt(11, 10, 10, 0))
    h.say("tapos ko na yung backup")
    h.tap("CANCEL")
    assert rows(h, LogEntry) == []


def test_log_edit_flow(make):
    h = make(dt(11, 10, 10, 0))
    h.say("tapos ko na yung backup")
    h.tap("EDIT")
    h.say("natapos ko yung backup ng server")
    h.tap("CONFIRM")
    assert [e.raw_text for e in rows(h, LogEntry)] == ["natapos ko yung backup ng server"]


def test_typed_yes_confirms(make):
    h = make(dt(11, 10, 10, 0))
    h.say("tapos ko na yung backup")
    h.say("oo")
    assert len(rows(h, LogEntry)) == 1


def test_log_marks_matching_task_done(make):
    h = make(dt(11, 10, 10, 0))
    h.say("gawa login bug")
    assert rows(h, Task)[0].title == "login bug"
    h.say("tapos ko na yung login bug")
    assert "task: login bug" in h.last()
    h.tap("CONFIRM")
    assert rows(h, Task)[0].status == "done"
    assert rows(h, LogEntry)[0].task_id == rows(h, Task)[0].id


def test_checkin_answer_and_carry_over(make):
    h = make(dt(11, 10, 16, 0))
    h.say("hi")
    h.say("gawa network audit")
    h.advance_to(dt(11, 10, 16, 30))
    assert any("Ano ang natapos mo" in t for t in h.texts())
    h.say("natapos ko yung backup, di pa yung network audit")
    assert "Hindi pa tapos" in h.last()
    h.tap("CONFIRM")
    assert [e.raw_text for e in rows(h, LogEntry)] == ["natapos ko yung backup"]
    assert rows(h, Task)[0].status == "open"  # carried over
    assert rows(h, DailyRun)[0].checkin_answered_at is not None


def test_not_done_without_task_becomes_carry_over_task(make):
    h = make(dt(11, 10, 16, 30))
    h.say("hi")
    h.advance_to(dt(11, 10, 16, 31))
    h.say("di pa yung firewall rules")
    h.tap("CONFIRM")
    assert any("firewall rules" in t.title for t in rows(h, Task))


def test_skip_in_checkin_counts_as_answer(make):
    h = make(dt(11, 10, 16, 0))
    h.say("hi")
    h.advance_to(dt(11, 10, 16, 31))
    h.say("wala")
    assert rows(h, DailyRun)[0].checkin_answered_at is not None
    n = len(h.texts())
    h.advance_to(dt(11, 10, 20, 0))
    assert not any("Ano nga pala" in t for t in h.texts()[n:])


# ------------------------------------------------------------------ Phase 2: reminders / briefing

def test_reminder_is_saved_and_delivered(make):
    h = make(dt(11, 9, 10, 0))
    out = h.say("paalala mo ko bukas 3pm magpasa ng report")
    assert "magpasa ng report" in out[-1]
    r = rows(h, Reminder)[0]
    assert r.remind_at == dt(11, 10, 15, 0)
    h.say("hello")  # keeps window open
    h.advance_to(dt(11, 10, 9, 0))
    h.say("hi")
    h.advance_to(dt(11, 10, 15, 5))
    assert any("⏰ Paalala: magpasa ng report" in t for t in h.texts())


def test_ambiguous_time_asks_confirmation(make):
    h = make(dt(11, 9, 10, 0))
    h.say("paalala mo ko bukas magpasa ng report")  # no time
    assert "Tama ba" in h.last()
    assert rows(h, Reminder) == []
    h.tap("CONFIRM")
    assert rows(h, Reminder)[0].remind_at == dt(11, 10, 9, 0)


def test_missing_time_is_asked(make):
    h = make(dt(11, 9, 10, 0))
    h.say("paalala mo ko magpasa ng report")
    assert "Kailan" in h.last()
    h.say("bukas 3pm")
    assert rows(h, Reminder)[0].remind_at == dt(11, 10, 15, 0)


def test_meeting_reminders_30_and_10(make):
    h = make(dt(11, 9, 8, 0))
    h.say("may meeting sa Huwebes 10am with the director")
    m = rows(h, Meeting)[0]
    assert m.start_at == dt(11, 12, 10, 0)
    keys = sorted(r.dedup_key for r in rows(h, Outbox, Outbox.kind == "meeting"))
    assert keys == [f"meet:{m.id}:10", f"meet:{m.id}:30"]
    h.advance_to(dt(11, 12, 9, 29))  # window long closed unless refreshed
    assert not any("min na lang" in t for t in h.texts())


def test_meeting_reminders_arrive_when_window_open(make):
    h = make(dt(11, 12, 8, 0))
    h.say("may meeting ngayon 10am with the director")
    h.advance_to(dt(11, 12, 9, 51))
    assert h.sent_at("30 min na lang") == [dt(11, 12, 9, 30)]
    assert h.sent_at("10 min na lang") == [dt(11, 12, 9, 50)]


def test_briefing_contents(make):
    h = make(dt(11, 10, 6, 55))
    h.say("hi")
    h.say("gawa AR ngayong Nobyembre")
    h.tap("CONFIRM")
    h.say("may meeting ngayon 10am with the director")
    h.say("paalala mo ko ngayon 2pm magpasa ng report")
    h.advance_to(dt(11, 10, 7, 30))
    b = h.sent_at("Magandang umaga")
    assert b == [dt(11, 10, 7, 30)]
    text = [t for t in h.texts() if "Magandang umaga" in t][0]
    assert "AR" in text and "director" in text and "magpasa ng report" in text


def test_unfinished_yesterday_is_carried_into_briefing(make):
    h = make(dt(11, 10, 15, 0))
    h.say("gawa network audit")
    h.say("hi")
    h.advance_to(dt(11, 11, 6, 50))
    h.say("hello")
    h.advance_to(dt(11, 11, 7, 30))
    text = [t for t in h.texts() if "Magandang umaga" in t][-1]
    assert "Unfinished" in text and "network audit" in text


def test_briefing_followups_until_reply(make):
    h = make(dt(11, 10, 6, 55))
    h.say("hi")
    h.advance_to(dt(11, 10, 14, 0))
    assert h.sent_at("Nasa taas ang briefing") == [dt(11, 10, 8, 30), dt(11, 10, 9, 30),
                                                    dt(11, 10, 10, 30), dt(11, 10, 11, 30)]


def test_any_reply_stops_briefing_followups(make):
    h = make(dt(11, 10, 6, 55))
    h.say("hi")
    h.advance_to(dt(11, 10, 9, 0))
    assert len(h.sent_at("Nasa taas ang briefing")) == 1
    h.say("gising na")
    h.advance_to(dt(11, 10, 12, 30))
    assert len(h.sent_at("Nasa taas ang briefing")) == 1


def test_checkin_followups_hourly_until_answer_but_not_stopped_by_other_msgs(make):
    h = make(dt(11, 10, 16, 0))
    h.say("hi")
    h.advance_to(dt(11, 10, 18, 40))
    assert h.sent_at("Ano ang natapos mo today") == [dt(11, 10, 16, 30)]
    assert h.sent_at("Ano nga pala ang natapos") == [dt(11, 10, 17, 30), dt(11, 10, 18, 30)]
    h.say("paalala mo ko bukas 9am tumawag")  # not a check-in answer
    h.advance_to(dt(11, 10, 19, 40))
    assert len(h.sent_at("Ano nga pala ang natapos")) == 3
    h.say("natapos ko yung backup")
    h.tap("CONFIRM")
    h.advance_to(dt(11, 10, 23, 0))
    assert len(h.sent_at("Ano nga pala ang natapos")) == 3


def test_last_checkin_followup_is_2230(make):
    h = make(dt(11, 10, 16, 0))
    h.say("hi")
    h.advance_to(dt(11, 10, 23, 30))
    t = h.sent_at("Ano nga pala ang natapos")
    assert t[-1] == dt(11, 10, 22, 30) and len(t) == 5 + 1  # 17:30..22:30


def test_late_briefing_on_first_inbound(make):
    h = make(dt(11, 10, 6, 40))  # bot "off" at 07:30; window closed
    h.advance_to(dt(11, 10, 7, 45))
    assert not h.texts()
    out = h.say("hi")
    assert any("Magandang umaga" in t for t in out)
    n = len([t for t in h.texts() if "Magandang umaga" in t])
    h.say("hello ulit")
    assert len([t for t in h.texts() if "Magandang umaga" in t]) == n  # only once


def test_late_briefing_skipped_after_cutoff(make):
    h = make(dt(11, 10, 17, 0))
    out = h.say("hi")
    assert not any("Magandang umaga" in t for t in out)


def test_restart_sweep_sends_missed_briefing_once_if_window_open(make):
    h = make(dt(11, 9, 22, 0))
    h.say("hi")  # window open until Tue 21:00
    h.clock.set(dt(11, 10, 8, 10))  # PC was off at 07:30
    h.bot.tick()
    assert len([t for t in h.texts() if "Magandang umaga" in t]) == 1
    h.bot.tick()
    h.bot.tick()
    assert len([t for t in h.texts() if "Magandang umaga" in t]) == 1


def test_duplicate_event_ignored(make):
    h = make(dt(11, 10, 10, 0))
    ev = h._ev("message", text="tapos ko na yung backup")
    assert h.bot.on_inbound(ev) is True
    n = len(h.texts())
    assert h.bot.on_inbound(ev) is False
    assert len(h.texts()) == n


def test_agenda_query(make):
    h = make(dt(11, 9, 10, 0))
    h.say("may meeting bukas 10am with the director")
    out = h.say("ano sched ko bukas?")
    assert "director" in out[-1]


def test_cancel_reminder(make):
    h = make(dt(11, 9, 10, 0))
    h.say("paalala mo ko bukas 3pm magpasa ng report")
    h.say("cancel mo yung reminder sa report")
    assert rows(h, Reminder)[0].status == "cancelled"
    assert rows(h, Outbox, Outbox.kind == "reminder")[0].status == "cancelled"


# ------------------------------------------------------------------ Phase 3: modes

def test_weekend_default_is_quiet_except_warnings(make):
    h = make(dt(11, 14, 6, 0))  # Saturday
    h.say("hi")
    h.advance_to(dt(11, 14, 23, 0))
    texts = h.texts()
    assert not any("Magandang umaga" in t or "natapos mo" in t for t in texts)
    assert any("Magsasara na ang chat window" in t for t in texts)


def test_aral_saturday_runs_normal_flow_with_school_context(make):
    h = make(dt(11, 13, 20, 0))
    out = h.say("mag-aaral ako ngayong Sabado")
    assert "Aral" in out[-1]
    assert rows(h, DayMode)[0].mode == "aral"
    h.advance_to(dt(11, 14, 6, 50))
    h.say("hi")
    h.advance_to(dt(11, 14, 7, 31))
    assert any("Magandang umaga" in t for t in h.texts())
    h.say("natapos ko yung chapter 3 ng reviewer")
    assert "school" in h.last()
    h.tap("CONFIRM")
    assert rows(h, LogEntry)[0].context == "school"


def test_leave_tomorrow_cancels_flow(make):
    h = make(dt(11, 9, 20, 0))
    h.say("naka-leave ako bukas")
    assert rows(h, DayMode)[0].mode == "leave"
    h.say("hi")
    h.advance_to(dt(11, 10, 10, 0))
    assert not any("Magandang umaga" in t for t in h.texts())


def test_holiday_is_off_by_default(make):
    h = make(dt(11, 29, 20, 0))
    h.say("hi")
    h.advance_to(dt(11, 30, 8, 0))  # Bonifacio Day 2026-11-30
    assert not any("Magandang umaga" in t for t in h.texts())


def test_friday_checkin_asks_weekend_per_day(make):
    h = make(dt(11, 13, 16, 0))
    h.say("hi")
    h.advance_to(dt(11, 13, 16, 30))
    last = h.channel.sent[-1]
    assert "Sabado" in last["text"]
    assert [q["payload"] for q in last["quick_replies"]] == [
        "MODE:2026-11-14:work", "MODE:2026-11-14:aral", "MODE:2026-11-14:off"]
    out = h.tap("MODE:2026-11-14:aral")
    assert any("Linggo" in t or "Nov 15" in t for t in out)
    h.tap("MODE:2026-11-15:off")
    modes = {d.date.isoformat(): d.mode for d in rows(h, DayMode)}
    assert modes == {"2026-11-14": "aral", "2026-11-15": "off"}
