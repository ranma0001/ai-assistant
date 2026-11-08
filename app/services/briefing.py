from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Meeting, Reminder, Task
from app.services import daymode
from app.timeutil import TZ, fmt_day, fmt_time


def _day_bounds(d: date) -> tuple[datetime, datetime]:
    a = datetime(d.year, d.month, d.day, tzinfo=TZ)
    return a, a + timedelta(days=1)


def day_items(s: Session, d: date):
    """Return (today, carryover, soon, meetings, reminders, later) for date d."""
    a, b = _day_bounds(d)
    today_t, carry, soon, later = [], [], [], []
    for t in s.scalars(select(Task).where(Task.status == "open").order_by(Task.due_at, Task.id)):
        if t.context != "work" and daymode.get_mode(s, d) != "aral":
            continue
        if t.due_at is None:
            (carry if t.created_at < a else today_t).append(t)
        elif t.due_at < a:
            carry.append(t)
        elif t.due_at < b:
            today_t.append(t)
        elif t.due_at < b + timedelta(days=3):
            soon.append(t)
        else:
            later.append(t)
    meetings = list(s.scalars(select(Meeting).where(
        Meeting.status == "active", Meeting.start_at >= a, Meeting.start_at < b).order_by(Meeting.start_at)))
    reminders = list(s.scalars(select(Reminder).where(
        Reminder.status == "active", Reminder.remind_at >= a, Reminder.remind_at < b).order_by(Reminder.remind_at)))
    return today_t, carry, soon, meetings, reminders, later


def _due_label(t: Task) -> str:
    if not t.due_at:
        return ""
    if t.due_granularity == "month":
        return f" (due {t.due_at:%b} {t.due_at.day})"
    if t.due_granularity in ("day", "week") or (t.due_at.hour == 0 and t.due_at.minute == 0):
        return f" (due {fmt_day(t.due_at)})"
    return f" (due {fmt_day(t.due_at)} {fmt_time(t.due_at)})"


def build_briefing(s: Session, d: date) -> str:
    today_t, carry, soon, meetings, reminders, later = day_items(s, d)
    mode = daymode.get_mode(s, d)
    head = f"☀️ Magandang umaga! {fmt_day(d)}"
    if mode == "aral":
        head += " · 📚 Aral day"
    lines = [head, ""]
    if meetings:
        lines.append("📅 Meetings:")
        lines += [f"• {fmt_time(m.start_at)} – {m.title}" for m in meetings]
        lines.append("")
    if reminders:
        lines.append("⏰ Reminders:")
        lines += [f"• {fmt_time(r.remind_at)} – {r.title}" for r in reminders]
        lines.append("")
    if today_t:
        lines.append("📋 Task ngayong araw:")
        lines += [f"• {t.title}{_due_label(t)}" for t in today_t]
        lines.append("")
    if carry:
        lines.append("↩️ Unfinished mula sa nakaraan:")
        lines += [f"• {t.title} (mula {fmt_day(t.created_at if not t.due_at else t.due_at)})" for t in carry]
        lines.append("")
    if soon:
        lines.append("🔜 Malapit na ang due:")
        lines += [f"• {t.title}{_due_label(t)}" for t in soon]
        lines.append("")
    if later:
        lines.append("🗂️ Mga paparating na due:")
        lines += [f"• {t.title}{_due_label(t)}" for t in later[:5]]
        lines.append("")
    if not (meetings or reminders or today_t or carry or soon or later):
        lines.append("Wala pang nakalistang task o meeting. Sabihin mo lang kung may idadagdag! 🙂")
    else:
        lines.append("Reply ka lang para malaman kong gising ka na. 👍")
    return "\n".join(lines).strip()


def build_agenda(s: Session, d: date) -> str:
    today_t, carry, soon, meetings, reminders, later = day_items(s, d)
    lines = [f"🗓️ {fmt_day(d)}", ""]
    if meetings:
        lines += ["Meetings:"] + [f"• {fmt_time(m.start_at)} – {m.title}" for m in meetings] + [""]
    if reminders:
        lines += ["Reminders:"] + [f"• {fmt_time(r.remind_at)} – {r.title}" for r in reminders] + [""]
    if today_t or carry:
        lines += ["Tasks:"] + [f"• {t.title}{_due_label(t)}" for t in today_t + carry] + [""]
    if len(lines) == 2:
        lines.append("Wala akong nakalistang schedule para dito.")
    return "\n".join(lines).strip()
