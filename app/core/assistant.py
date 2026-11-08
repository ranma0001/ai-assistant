"""The assistant core. Channel-agnostic: Messenger, console and tests all drive this same class.

Rules enforced here:
  * every outbound goes through `_deliver` -> window check (23h) / quiet hours / expiry / pending queue
  * only INBOUND user actions update last_inbound_at (nudges and warnings do not)
  * nothing is saved from a free-text parse without being echoed (and confirmed when uncertain)
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.core.channel import Channel, InboundEvent, QuickReplies
from app.core.notify import email_enabled, send_email
from app.db.models import (
    ConvState, DailyRun, InboxEvent, LogEntry, Meeting, Message, Outbox, Reminder, Task, UserState,
)
from app.llm.parsers import IntentParser
from app.llm.schemas import Intent
from app.nlp.when_parser import Resolved, parse_when, resolve_range
from app.reports.builder import build_report
from app.services import briefing, calendar_sync, daymode
from app.services.tasks import match_task
from app.timeutil import Clock, at, fmt_day, fmt_time, hhmm, in_quiet, shift_quiet, window_open

log = logging.getLogger(__name__)

CONFIRM_QR: QuickReplies = [
    {"title": "✅ Oo", "payload": "CONFIRM"},
    {"title": "✏️ Baguhin", "payload": "EDIT"},
    {"title": "❌ Hindi", "payload": "CANCEL"},
]
YES = {"oo", "opo", "yes", "y", "ok", "okay", "sige", "tama", "save", "confirm", "yep", "yup"}
NO = {"hindi", "no", "n", "cancel", "wag", "huwag", "ayaw", "di", "hindi po"}
EDIT = {"edit", "baguhin", "ayusin", "palitan"}
HELP = (
    "Hindi ko masyadong nakuha 🤔. Pwede mong sabihin ang mga ito:\n"
    "• “paalala mo ko bukas 3pm magpasa ng report”\n"
    "• “may meeting sa Huwebes 10am with the director”\n"
    "• “tapos ko na yung login bug” (para i-log)\n"
    "• “mag-aaral ako ngayong Sabado” / “naka-leave ako bukas”\n"
    "• “ano sched ko bukas?” · “gawa mo report ng Oktubre”"
)
SKIP_FOR_KINDS = {"warning"}


def _clip(s: str, n: int = 1900) -> list[str]:
    if len(s) <= n:
        return [s]
    parts, cur = [], ""
    for line in s.split("\n"):
        if len(cur) + len(line) + 1 > n:
            parts.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur.rstrip())
    return parts


class Assistant:
    def __init__(self, settings: Settings, sessions: sessionmaker[Session], channel: Channel,
                 clock: Clock, parser: IntentParser, formalizer=None):  # noqa: ANN001
        self.cfg = settings
        self.sessions = sessions
        self.channel = channel
        self.clock = clock
        self.parser = parser
        self.formalizer = formalizer
        self.psid = settings.owner_psid
        self.lock = threading.RLock()
        self._seeded: tuple | None = None
        self._dirty = 0
        self._last_cal_sync: datetime | None = None
        # injectable for tests: () -> list of ICS texts
        self.calendar_fetch = lambda: calendar_sync.fetch_ics(
            [u.strip() for u in self.cfg.calendar_ics_urls.split(",") if u.strip()])

    # ------------------------------------------------------------------ small helpers
    def _state(self, s: Session) -> UserState:
        st = s.get(UserState, self.psid)
        if not st:
            st = UserState(psid=self.psid, prefs={})
            s.add(st)
            s.flush()
        return st

    def _conv(self, s: Session) -> ConvState:
        c = s.get(ConvState, self.psid)
        if not c:
            c = ConvState(psid=self.psid, state="idle", draft={})
            s.add(c)
            s.flush()
        return c

    def _run(self, s: Session, d: date) -> DailyRun:
        r = s.get(DailyRun, d)
        if not r:
            r = DailyRun(date=d)
            s.add(r)
            s.flush()
        return r

    def window_ok(self, s: Session, now: datetime) -> bool:
        return window_open(self._state(s).last_inbound_at, now, self.cfg.window_limit_hours)

    def _log(self, s: Session, direction: str, text: str, intent: str | None = None, parse: dict | None = None):
        s.add(Message(direction=direction, text=text, intent=intent, parse=parse, ts=self.clock.now()))

    def _send(self, s: Session, text: str, qr: QuickReplies | None = None, *, kind: str = "reply") -> bool:
        """Send now, through the gate. Replies to an inbound are always inside the window."""
        now = self.clock.now()
        if not self.window_ok(s, now):
            self._enqueue(s, "pending_reply", text, now, quick=qr, dedup=None, shift=False, status="pending")
            return False
        chunks = _clip(text)
        for i, c in enumerate(chunks):
            self.channel.send(self.psid, c, qr if i == len(chunks) - 1 else None)
        self._log(s, "out", text)
        return True

    def _enqueue(self, s: Session, kind: str, text: str, due: datetime, *, expires: datetime | None = None,
                 quick: QuickReplies | None = None, dedup: str | None = None, ref_id: int | None = None,
                 ref_date: date | None = None, urgent: bool = False, shift: bool = True,
                 status: str = "scheduled") -> Outbox | None:
        now = self.clock.now()
        if dedup:
            existing = s.scalar(select(Outbox).where(Outbox.dedup_key == dedup))
            if existing:
                return None
        orig = due
        if shift and in_quiet(due, self.cfg.quiet_start_hour, self.cfg.quiet_end_hour):
            due = shift_quiet(due, self.cfg.quiet_start_hour, self.cfg.quiet_end_hour, self.cfg.quiet_shift_hour)
            if due < now:
                due = now
            if kind in ("reminder", "meeting") and due != orig:
                text += f"\n(para sa {fmt_day(orig)} {fmt_time(orig)})"
        row = Outbox(kind=kind, text=text, quick_replies=quick, due_at=due, expires_at=expires, dedup_key=dedup,
                     status=status, urgent=urgent, ref_id=ref_id, ref_date=ref_date, created_at=now)
        s.add(row)
        s.flush()
        return row

    # ------------------------------------------------------------------ INBOUND
    def on_inbound(self, ev: InboundEvent) -> bool:
        """Handle one user action. Returns False if it was a duplicate delivery."""
        with self.lock, self.sessions() as s:
            now = self.clock.now()
            if s.scalar(select(InboxEvent).where(InboxEvent.dedup_key == ev.dedup_key)):
                log.info("duplicate event ignored: %s", ev.dedup_key)
                return False
            s.add(InboxEvent(dedup_key=ev.dedup_key, kind=ev.kind, raw=ev.raw or {"text": ev.text},
                             received_at=now))
            st = self._state(s)
            st.last_inbound_at, st.last_inbound_kind = now, ev.kind
            self.reschedule_warning(s, now)
            s.flush()
            try:
                self._deliver_pending(s, now)
                self._late_briefing(s, now)
                if ev.kind == "reaction":
                    pass  # a reaction only keeps the window open
                else:
                    self._handle(s, ev, now)
            finally:
                row = s.scalar(select(InboxEvent).where(InboxEvent.dedup_key == ev.dedup_key))
                if row:
                    row.processed_at = self.clock.now()
                conv = self._conv(s)
                if conv.state != "idle":
                    conv.updated_at = now
                self._dirty += 1
                s.commit()
            return True

    # ---- window warning (one row, replaced on every inbound)
    def reschedule_warning(self, s: Session, now: datetime) -> None:
        due = shift_quiet(now + timedelta(hours=self.cfg.warning_after_hours),
                          self.cfg.quiet_start_hour, self.cfg.quiet_end_hour, self.cfg.quiet_shift_hour)
        expires = now + timedelta(hours=self.cfg.window_limit_hours)
        row = s.scalar(select(Outbox).where(Outbox.dedup_key == "warning"))
        if row:
            row.status, row.due_at, row.expires_at, row.sent_at, row.attempts = "scheduled", due, expires, None, 0
        else:
            s.add(Outbox(kind="warning", text="", quick_replies=None, due_at=due, expires_at=expires,
                         dedup_key="warning", status="scheduled", created_at=now))

    def _warning_text(self, s: Session, now: datetime) -> tuple[str, QuickReplies]:
        st = self._state(s)
        closes = (st.last_inbound_at or now) + timedelta(hours=24)
        lines = [f"⚠️ Magsasara na ang chat window ko mga {fmt_day(closes)} {fmt_time(closes)}. "
                 "Tap 👍 Tuloy para tuloy pa rin ang mga paalala ko sa'yo."]
        upcoming = self._upcoming(s, now, closes + timedelta(hours=12))
        if upcoming:
            lines.append("\nMay paparating pa:")
            lines += [f"• {fmt_day(t)} {fmt_time(t)} – {title}" for t, title in upcoming[:6]]
        return "\n".join(lines), [{"title": "👍 Tuloy", "payload": "KEEPALIVE"}]

    def _upcoming(self, s: Session, now: datetime, until: datetime) -> list[tuple[datetime, str]]:
        out = [(r.remind_at, r.title) for r in s.scalars(select(Reminder).where(
            Reminder.status == "active", Reminder.remind_at > now, Reminder.remind_at <= until))]
        out += [(m.start_at, m.title) for m in s.scalars(select(Meeting).where(
            Meeting.status == "active", Meeting.start_at > now, Meeting.start_at <= until))]
        d = now.date()
        for i in range(0, 3):
            day = d + timedelta(days=i)
            t = at(day, self.cfg.briefing_time)
            if now < t <= until and daymode.is_active(s, day):
                out.append((t, "Morning briefing"))
                break
        return sorted(out)

    # ---- pending queue / late briefing
    def _deliver_pending(self, s: Session, now: datetime) -> None:
        rows = list(s.scalars(select(Outbox).where(Outbox.status == "pending").order_by(Outbox.due_at)))
        if not rows:
            return
        lines, resend_checkin = [], False
        for r in rows:
            if r.kind in ("reminder", "meeting"):
                lines.append(f"• {fmt_day(r.due_at)} {fmt_time(r.due_at)} – {self._plain(r)}")
                r.status, r.sent_at = "sent", now
                if r.kind == "reminder" and r.ref_id:
                    rem = s.get(Reminder, r.ref_id)
                    if rem and rem.status == "active":
                        rem.status = "sent"
            elif r.kind == "pending_reply":
                lines.append(f"• {r.text[:200]}")
                r.status, r.sent_at = "sent", now
            elif r.kind == "checkin" and r.ref_date == now.date():
                resend_checkin = True
                r.status = "expired"
            else:
                r.status = "expired"  # briefings are regenerated (late briefing); the rest is stale
        if lines:
            self._send(s, "Na-miss mo:\n" + "\n".join(lines))
        if resend_checkin:
            run = self._run(s, now.date())
            if not run.checkin_answered_at and daymode.is_active(s, now.date()):
                self._send_checkin(s, now.date(), now)

    @staticmethod
    def _plain(r: Outbox) -> str:
        t = re.sub(r"^[^\w]*(Paalala|Na-miss mo)[^:]*:\s*", "", r.text.split("\n")[0])
        return t.strip()

    def _late_briefing(self, s: Session, now: datetime) -> None:
        d = now.date()
        if not daymode.is_active(s, d):
            return
        run = self._run(s, d)
        if run.briefing_sent_at:
            return
        if not (at(d, self.cfg.briefing_time) <= now < at(d, self.cfg.late_briefing_cutoff)):
            return
        self._send(s, briefing.build_briefing(s, d))
        run.briefing_sent_at = now
        for r in s.scalars(select(Outbox).where(Outbox.dedup_key == f"briefing:{d}")):
            if r.status in ("scheduled", "pending"):
                r.status = "cancelled"

    # ---- message handling
    def _checkin_pending(self, s: Session, now: datetime) -> bool:
        run = s.get(DailyRun, now.date())
        return bool(run and run.checkin_prompted_at and not run.checkin_answered_at)

    def _handle(self, s: Session, ev: InboundEvent, now: datetime) -> None:
        conv = self._conv(s)
        text = (ev.text or "").strip()
        payload = ev.payload
        self._log(s, "in", text or (payload or ""))

        if payload == "KEEPALIVE":
            self._send(s, "Sige, tuloy tayo! ✅ Bukas pa ang chat window ng 24 oras mula ngayon.")
            return
        if payload and payload.startswith("MODE:"):
            _, ds, mode = payload.split(":")
            self._apply_mode_choice(s, date.fromisoformat(ds), mode, now)
            return

        low = text.lower().strip(" .!")
        if conv.state == "confirming":
            if payload == "CONFIRM" or low in YES:
                self._commit(s, conv, now)
                return
            if payload == "CANCEL" or low in NO:
                conv.state, conv.draft = "idle", {}
                self._send(s, "Okay, hindi ko sine-save. 👍")
                return
            if payload == "EDIT" or low in EDIT:
                conv.state, conv.draft = "idle", {}
                self._send(s, "Sige, ipadala mo ulit ang tamang bersyon.")
                return
            conv.state, conv.draft = "idle", {}  # something new: drop the draft and treat as new input
        elif payload in ("CONFIRM", "CANCEL", "EDIT"):
            self._send(s, "Wala akong hinihintay na kumpirmasyon ngayon.")
            return

        if conv.state == "awaiting_when":
            draft = conv.draft
            res = parse_when(text, now, self.cfg.default_reminder_hour)
            if res:
                conv.state = "idle"
                self._stage_timed(s, conv, draft["kind"], draft["title"], res, now, draft.get("attendees", []),
                                  draft.get("raw", ""))
                return
            conv.state, conv.draft = "idle", {}

        hint = "checkin" if self._checkin_pending(s, now) else None
        intent = self.parser.parse(text, hint)
        self._log_parse(s, text, intent)
        self._dispatch(s, conv, intent, text, now, hint)

    def _log_parse(self, s: Session, text: str, it: Intent) -> None:
        m = s.scalar(select(Message).where(Message.direction == "in").order_by(Message.id.desc()))
        if m:
            m.intent, m.parse = it.intent, it.model_dump()

    def _dispatch(self, s: Session, conv: ConvState, it: Intent, text: str, now: datetime, hint: str | None):
        name = it.intent
        if name in ("log_activity", "checkin_answer"):
            return self._stage_log(s, conv, it, now, source="checkin" if hint == "checkin" else "adhoc")
        if name == "checkin_skip":
            if hint == "checkin":
                run = self._run(s, now.date())
                run.checkin_answered_at = now
                open_n = len(list(s.scalars(select(Task).where(Task.status == "open"))))
                msg = "Okay, skip natin ang check-in. 👍"
                if open_n:
                    msg += f" May {open_n} open task pa na ihahatid ko sa susunod na briefing."
                self._send(s, msg)
                return self._after_checkin(s, now)
            return self._send(s, HELP)
        if name in ("set_reminder", "add_meeting"):
            kind = "reminder" if name == "set_reminder" else "meeting"
            if not it.title:
                return self._send(s, "Ano ang ipapaalala ko? Sabihin mo ulit with details. 🙂")
            if not it.when_text:
                conv.state, conv.draft = "awaiting_when", {"kind": kind, "title": it.title,
                                                          "attendees": it.attendees, "raw": text}
                return self._send(s, "Kailan? (hal. “bukas 3pm” o “sa Huwebes 10am”)")
            res = parse_when(it.when_text, now, self.cfg.default_reminder_hour)
            if res is None:
                conv.state, conv.draft = "awaiting_when", {"kind": kind, "title": it.title,
                                                          "attendees": it.attendees, "raw": text}
                return self._send(s, f"Hindi ko naintindihan ang “{it.when_text}”. Anong petsa/oras? "
                                     "(hal. “bukas 3pm”)")
            return self._stage_timed(s, conv, kind, it.title, res, now, it.attendees, text, it.confidence)
        if name == "add_task":
            return self._stage_task(s, conv, it, now, text)
        if name == "set_day_mode":
            return self._set_day_mode(s, it, now)
        if name == "complete_task":
            t = match_task(s, it.task_ref or text)
            if not t:
                return self._send(s, "Hindi ko makita kung aling task iyon. Pakisabi ulit ang pangalan ng task.")
            t.status, t.done_at = "done", now
            return self._send(s, f"✅ Done: {t.title}")
        if name == "cancel_or_edit":
            return self._cancel(s, it, text, now)
        if name == "snooze":
            return self._snooze(s, it, now)
        if name == "query_agenda":
            d = now.date()
            if it.when_text:
                r = parse_when(it.when_text, now)
                d = r.dt.date() if r else d
            return self._send(s, briefing.build_agenda(s, d))
        if name == "generate_report":
            return self._report(s, it, text, now)
        return self._send(s, HELP)

    # ---- staging (draft -> confirm) and committing
    def _stage_timed(self, s, conv, kind, title, res: Resolved, now, attendees, raw, confidence=1.0):  # noqa: ANN001
        when = res.dt
        past = when <= now
        draft = {"kind": kind, "title": title, "when": when.isoformat(), "attendees": attendees or [], "raw": raw}
        if past:
            conv.state, conv.draft = "confirming", draft
            return self._send(s, f"Lagpas na yata ang {fmt_day(when)} {fmt_time(when)}. Tuloy pa rin ba ang "
                                 f"“{title}”?", CONFIRM_QR)
        if res.ambiguous or confidence < 0.7:
            conv.state, conv.draft = "confirming", draft
            why = f" ({res.note})" if res.note else ""
            icon = "📅" if kind == "meeting" else "⏰"
            return self._send(s, f"{icon} {title}\n🕒 {fmt_day(when)} {fmt_time(when)}{why}\nTama ba?", CONFIRM_QR)
        self._commit_draft(s, draft, now)

    def _stage_task(self, s, conv, it: Intent, now, raw):  # noqa: ANN001
        title = (it.title or raw).strip()
        draft = {"kind": "task", "title": title, "raw": raw, "due": None, "gran": None}
        confirm = it.confidence < 0.7
        if it.due_text:
            res = parse_when(it.due_text, now)
            if res:
                draft["due"], draft["gran"] = res.dt.isoformat(), res.granularity
                confirm = confirm or res.granularity in ("month", "week") or res.ambiguous
        if confirm:
            conv.state, conv.draft = "confirming", draft
            due = f"\n📆 Due: {fmt_day(datetime.fromisoformat(draft['due']))}" if draft["due"] else ""
            return self._send(s, f"📋 Bagong task: {title}{due}\nTama ba?", CONFIRM_QR)
        self._commit_draft(s, draft, now)

    def _stage_log(self, s, conv, it: Intent, now, source: str):  # noqa: ANN001
        d = now.date()
        ctx = daymode.context_for(s, d)
        items, notes, new_tasks = [], [], []
        for e in it.entries:
            t = match_task(s, e.raw)
            items.append({"raw": e.raw, "status": e.status, "task_id": t.id if t else None,
                          "task": t.title if t else None})
        if not items:
            if source == "checkin":
                conv.state = "idle"
                return self._send(s, "Hindi ko nakuha kung ano ang natapos mo. Pakisabi ulit, o “wala”/“skip” "
                                     "kung wala. 🙂")
            return self._send(s, HELP)
        for i in items:
            if i["status"] == "not_done" and not i["task_id"]:
                new_tasks.append(i["raw"])
        draft = {"kind": "log", "source": source, "date": d.isoformat(), "context": ctx, "items": items}
        conv.state, conv.draft = "confirming", draft
        lines = ["Ito ang naintindihan ko:"]
        for i in items:
            if i["status"] == "not_done":
                lines.append(f"⏳ Hindi pa tapos: {i['raw']}" + (f" → task: {i['task']}" if i["task"] else
                                                               " → gagawin kong task (carry-over)"))
            else:
                mark = "✅" if i["status"] == "done" else "🔄"
                lines.append(f"{mark} {i['raw']}" + (f" → task: {i['task']}" if i["task"] else ""))
        lines.append("📚 Context: school (hindi isasama sa work report)" if ctx == "school" else "💼 Context: work")
        lines.append("Tama ba?")
        self._send(s, "\n".join(lines), CONFIRM_QR)

    def _commit(self, s: Session, conv: ConvState, now: datetime) -> None:
        draft = conv.draft
        conv.state, conv.draft = "idle", {}
        self._commit_draft(s, draft, now)

    def _commit_draft(self, s: Session, d: dict, now: datetime) -> None:
        kind = d.get("kind")
        if kind == "reminder":
            when = datetime.fromisoformat(d["when"])
            rem = Reminder(title=d["title"], raw_text=d.get("raw", ""), remind_at=when, created_at=now)
            s.add(rem)
            s.flush()
            exp = when + timedelta(minutes=self.cfg.reminder_grace_minutes)
            row = self._enqueue(s, "reminder", f"⏰ Paalala: {d['title']}", when, expires=exp,
                                dedup=f"reminder:{rem.id}", ref_id=rem.id)
            shifted = row is not None and row.due_at != when
            extra = f"\n(Ipapadala ko ito nang {fmt_time(row.due_at)} ng {fmt_day(row.due_at)} dahil quiet hours.)" \
                if shifted else ""
            self._send(s, f"✅ Sige! Ipapaalala ko: {d['title']}\n🕒 {fmt_day(when)} {fmt_time(when)}{extra}")
        elif kind == "meeting":
            start = datetime.fromisoformat(d["when"])
            m = Meeting(title=d["title"], start_at=start,
                        end_at=start + timedelta(minutes=self.cfg.default_meeting_minutes),
                        attendees=d.get("attendees", []), source="chat")
            s.add(m)
            s.flush()
            leads = self._schedule_meeting(s, m, now)
            tail = f"\nPaalalahanan kita {' at '.join(leads)} min bago." if leads else ""
            self._send(s, f"✅ Naka-save ang meeting: {m.title}\n🕒 {fmt_day(start)} {fmt_time(start)}{tail}")
        elif kind == "task":
            due = datetime.fromisoformat(d["due"]) if d.get("due") else None
            ctx = daymode.context_for(s, now.date())
            t = Task(title=d["title"], raw_text=d.get("raw", ""), due_at=due, due_granularity=d.get("gran"),
                     context=ctx, created_at=now)
            s.add(t)
            tail = f"\n📆 Due: {fmt_day(due)}" if due else ""
            self._send(s, f"✅ Naka-save na task: {t.title}{tail}")
        elif kind == "log":
            self._commit_log(s, d, now)

    def _commit_log(self, s: Session, d: dict, now: datetime) -> None:
        day = date.fromisoformat(d["date"])
        done = carried = 0
        for i in d["items"]:
            task = s.get(Task, i["task_id"]) if i.get("task_id") else None
            if i["status"] in ("done", "ongoing"):
                s.add(LogEntry(raw_text=i["raw"], summary=i["raw"], log_date=day, context=d["context"],
                               task_id=task.id if task else None, source=d["source"], created_at=now))
                if task and i["status"] == "done":
                    task.status, task.done_at = "done", now
                done += 1
            else:
                if not task:
                    s.add(Task(title=re.sub(r"^(di|hindi|wala) pa( ko)?( natapos| tapos)?( yung| ang| ung)?\s*", "",
                                            i["raw"], flags=re.I).strip() or i["raw"], raw_text=i["raw"],
                               context=d["context"], created_at=now))
                carried += 1
        msg = f"✅ Na-log ko ang {done} entry." if done else "✅ Noted."
        if carried:
            msg += f" {carried} task ang naka-carry over sa susunod na briefing."
        self._send(s, msg)
        if d["source"] == "checkin":
            self._run(s, day).checkin_answered_at = now
            self._after_checkin(s, now)

    def _after_checkin(self, s: Session, now: datetime) -> None:
        for r in s.scalars(select(Outbox).where(Outbox.kind == "cfu", Outbox.status == "scheduled",
                                                Outbox.ref_date == now.date())):
            r.status = "cancelled"

    # ---- other intents
    def _set_day_mode(self, s: Session, it: Intent, now: datetime) -> None:
        d = now.date()
        if it.date_text:
            res = parse_when(it.date_text, now)
            if res:
                d = res.dt.date()
        mode = it.mode or "work"
        daymode.set_mode(s, d, mode)
        extra = {"off": " Tahimik lang ako, maliban sa window warnings.",
                 "leave": " Walang briefing/check-in sa araw na iyon.",
                 "aral": " Gagana ang normal na flow; ang mga log ay context: school (wala sa work report).",
                 "work": " Gagana ang normal na flow."}[mode]
        self._send(s, f"Noted: {fmt_day(d)} = {daymode.LABEL[mode]}.{extra}")

    def _apply_mode_choice(self, s: Session, d: date, mode: str, now: datetime) -> None:
        daymode.set_mode(s, d, mode)
        self._send(s, f"Noted: {fmt_day(d)} = {daymode.LABEL[mode]}.")
        nxt = d + timedelta(days=1)
        if d.weekday() == 5 and not s.get(daymode.DayMode, nxt):
            self._send(s, f"At sa {fmt_day(nxt)}? May work o aral ka ba?", self._mode_qr(nxt))

    @staticmethod
    def _mode_qr(d: date) -> QuickReplies:
        return [{"title": "💼 Work", "payload": f"MODE:{d}:work"}, {"title": "📚 Aral", "payload": f"MODE:{d}:aral"},
                {"title": "😴 Off", "payload": f"MODE:{d}:off"}]

    def _cancel(self, s: Session, it: Intent, text: str, now: datetime) -> None:
        ref = it.target_ref or text
        from app.services.tasks import tokens
        q = tokens(ref)
        best, kind = None, None
        for r in s.scalars(select(Reminder).where(Reminder.status == "active")):
            if q & tokens(r.title):
                best, kind = r, "reminder"
        for m in s.scalars(select(Meeting).where(Meeting.status == "active", Meeting.start_at > now)):
            if q & tokens(m.title):
                best, kind = m, "meeting"
        t = match_task(s, ref)
        if best is None and t:
            best, kind = t, "task"
        if best is None:
            return self._send(s, "Hindi ko makita kung ano ang ika-cancel. Pakisabi ang pangalan.")
        if kind == "reminder":
            best.status = "cancelled"
            self._cancel_rows(s, f"reminder:{best.id}")
        elif kind == "meeting":
            best.status = "cancelled"
            for lead in self.cfg.lead_minutes():
                self._cancel_rows(s, f"meet:{best.id}:{lead}")
        else:
            best.status = "dropped"
        suffix = " Kung babaguhin ang oras, sabihin mo ulit na may bagong oras." if it.action == "edit" else ""
        self._send(s, f"🗑️ Na-cancel: {best.title}.{suffix}")

    @staticmethod
    def _cancel_rows(s: Session, dedup: str) -> None:
        for r in s.scalars(select(Outbox).where(Outbox.dedup_key == dedup, Outbox.status.in_(["scheduled", "pending"]))):
            r.status = "cancelled"

    def _snooze(self, s: Session, it: Intent, now: datetime) -> None:
        last = s.scalar(select(Outbox).where(Outbox.kind == "reminder", Outbox.status == "sent")
                        .order_by(Outbox.sent_at.desc()))
        if not last or not last.ref_id:
            return self._send(s, "Wala akong reminder na pwedeng i-snooze ngayon.")
        rem = s.get(Reminder, last.ref_id)
        res = parse_when(it.duration_text or it.when_text or "30 minuto", now)
        if not rem or not res:
            return self._send(s, "Hindi ko nakuha kung gaano katagal. Hal: “1 oras”.")
        self._commit_draft(s, {"kind": "reminder", "title": rem.title, "when": res.dt.isoformat(),
                               "raw": "snooze"}, now)

    def _report(self, s: Session, it: Intent, text: str, now: datetime) -> None:
        start, end, label = resolve_range(it.range_text or text, now.date())
        fmt = "xlsx" if re.search(r"\b(xlsx|excel|spreadsheet)\b", text, re.I) else "docx"
        path, n = build_report(s, start, end, fmt=fmt, settings=self.cfg, formalizer=self.formalizer, now=now)
        if not path:
            return self._send(s, f"Walang work log entries para sa {label} ({start:%b %d} – {end:%b %d}). "
                                 "Mag-log ka muna ng mga natapos mo. 🙂")
        self._send(s, f"📄 Draft ng accomplishment report para sa {label}: {n} entry.\n"
                      "Draft lang ito. Pakisuri bago i-submit. Naka-save sa:\n" + str(path))
        try:
            self.channel.send_file(self.psid, str(path), f"Accomplishment report – {label} (DRAFT)")
        except Exception as e:  # noqa: BLE001
            log.warning("send_file failed: %s", e)

    def _schedule_meeting(self, s: Session, m: Meeting, now: datetime) -> list[str]:
        """(Re)create the lead-time reminders of a meeting; returns the leads that are still in the future."""
        leads = []
        for lead in self.cfg.lead_minutes():
            due = m.start_at - timedelta(minutes=lead)
            if due <= now:
                continue
            self._enqueue(s, "meeting", f"📅 {lead} min na lang: {m.title} ({fmt_time(m.start_at)})", due,
                          expires=m.start_at, dedup=f"meet:{m.id}:{lead}", ref_id=m.id, urgent=True)
            leads.append(str(lead))
        return leads

    # ------------------------------------------------------------------ calendar sync (read-only ICS)
    def sync_calendar(self, s: Session, now: datetime) -> dict:
        """Mirror upcoming ICS events into Meeting rows. Only touches source='ics' rows; chat meetings are never
        changed. Events that vanished from the feed (or were cancelled) are cancelled here too."""
        texts = self.calendar_fetch()
        if not texts:
            return {"fetched": 0}
        a, b = calendar_sync.window(now, self.cfg.calendar_horizon_days)
        events: dict[str, calendar_sync.CalEvent] = {}
        for txt in texts:
            try:
                for e in calendar_sync.parse_events(txt, a, b):
                    events[e.external_id] = e
            except Exception:  # noqa: BLE001
                log.exception("calendar parse failed")
                return {"fetched": len(texts), "error": True}  # keep old state rather than cancel everything
        have = {m.external_id: m for m in s.scalars(select(Meeting).where(
            Meeting.source == "ics", Meeting.start_at >= a, Meeting.start_at < b))}
        added = changed = removed = 0
        for xid, e in events.items():
            m = have.get(xid)
            end = e.end or e.start + timedelta(minutes=self.cfg.default_meeting_minutes)
            if m is None:
                m = Meeting(title=e.title, start_at=e.start, end_at=end, attendees=list(e.attendees),
                            source="ics", external_id=xid)
                s.add(m)
                s.flush()
                added += 1
            elif m.status == "cancelled":
                continue  # cancelled in chat: do not resurrect
            elif m.title != e.title:
                m.title, changed = e.title, changed + 1
                for lead in self.cfg.lead_minutes():
                    self._cancel_rows(s, f"meet:{m.id}:{lead}")
            else:
                continue
            self._schedule_meeting(s, m, now)
        for xid, m in have.items():
            if xid not in events and m.status == "active":
                m.status = "cancelled"
                removed += 1
                for lead in self.cfg.lead_minutes():
                    self._cancel_rows(s, f"meet:{m.id}:{lead}")
        if added or changed or removed:
            self._dirty += 1
        return {"fetched": len(texts), "added": added, "changed": changed, "removed": removed}

    def _maybe_sync_calendar(self, s: Session, now: datetime) -> None:
        if not self.cfg.calendar_ics_urls.strip():
            return
        if self._last_cal_sync and now - self._last_cal_sync < timedelta(minutes=self.cfg.calendar_sync_minutes):
            return
        self._last_cal_sync = now
        try:
            res = self.sync_calendar(s, now)
            if res.get("added") or res.get("changed") or res.get("removed"):
                log.info("calendar sync: %s", res)
        except Exception:  # noqa: BLE001
            log.exception("calendar sync failed")

    # ------------------------------------------------------------------ TICK (scheduler)
    def tick(self) -> None:
        """Seed daily jobs, then deliver everything that is due. Safe to call at any frequency."""
        with self.lock, self.sessions() as s:
            now = self.clock.now()
            self._maybe_sync_calendar(s, now)
            key = (now.date(), self._dirty)
            if key != self._seeded:
                self._seed(s, now)
                self._seeded = key
            self._process_due(s, now)
            s.commit()

    def startup(self) -> str:
        """Missed-job sweep + status banner (printed when the bot is started manually)."""
        self.tick()
        with self.lock, self.sessions() as s:
            now = self.clock.now()
            st = self._state(s)
            ok = self.window_ok(s, now)
            pend = s.scalars(select(Outbox).where(Outbox.status == "pending")).all()
            li = f"{fmt_day(st.last_inbound_at)} {fmt_time(st.last_inbound_at)}" if st.last_inbound_at else "wala pa"
            closes = ""
            if ok and st.last_inbound_at:
                c = st.last_inbound_at + timedelta(hours=self.cfg.window_limit_hours)
                closes = f" (hanggang {fmt_day(c)} {fmt_time(c)})"
            return (f"[{now:%a %b %d %H:%M}] mode={daymode.get_mode(s, now.date())} · "
                    f"huling inbound: {li} · window: {'BUKAS' + closes if ok else 'SARADO'} · "
                    f"pending: {len(pend)}")

    def _seed(self, s: Session, now: datetime) -> None:
        for i in range(self.cfg.seed_days_ahead + 1):
            d = now.date() + timedelta(days=i)
            if not daymode.is_active(s, d):
                continue
            exp_brief = at(d, self.cfg.late_briefing_cutoff)
            self._enqueue(s, "briefing", "", at(d, self.cfg.briefing_time), expires=exp_brief,
                          dedup=f"briefing:{d}", ref_date=d, shift=False)
            step = timedelta(minutes=self.cfg.followup_interval_min)
            t, last = at(d, self.cfg.briefing_followup_first), at(d, self.cfg.briefing_followup_last)
            while t <= last:
                self._enqueue(s, "bfu", "☝️ Nasa taas ang briefing mo ngayong araw. Reply ka lang kapag nakita mo na. 🙂",
                              t, expires=t + step, dedup=f"bfu:{d}:{t:%H%M}", ref_date=d, shift=False)
                t += step
            self._enqueue(s, "checkin", "", at(d, self.cfg.checkin_time), expires=at(d, "23:00"),
                          dedup=f"checkin:{d}", ref_date=d, shift=False)
            t, last = at(d, self.cfg.checkin_followup_first), at(d, self.cfg.checkin_followup_last)
            while t <= last:
                self._enqueue(s, "cfu", "☝️ Ano nga pala ang natapos mo today? Sagot ka lang (o “wala”/“skip”). 🙂",
                              t, expires=t + step, dedup=f"cfu:{d}:{t:%H%M}", ref_date=d, shift=False)
                t += step
        s.flush()

    def _send_checkin(self, s: Session, d: date, now: datetime) -> bool:
        text = "📝 Ano ang natapos mo today? (Sabihin lang in your own words, o “wala”/“skip”.)"
        qr = None
        if d.weekday() == 4:
            sat = d + timedelta(days=1)
            if not s.get(daymode.DayMode, sat):
                text += "\n\nMay work o aral ka ba this weekend? Sabado muna:"
                qr = self._mode_qr(sat)
        ok = self._send(s, text, qr)
        if ok:
            self._run(s, d).checkin_prompted_at = now
        return ok

    def _process_due(self, s: Session, now: datetime) -> None:
        rows = list(s.scalars(select(Outbox).where(Outbox.status == "scheduled", Outbox.due_at <= now)
                              .order_by(Outbox.due_at, Outbox.id)))
        for r in rows:
            try:
                self._process_row(s, r, now)
            except Exception:  # noqa: BLE001
                log.exception("outbox row %s failed", r.id)
                r.attempts += 1
                if r.attempts >= 5:
                    r.status = "failed"

    def _process_row(self, s: Session, r: Outbox, now: datetime) -> None:  # noqa: C901
        kind, d = r.kind, r.ref_date
        st = self._state(s)
        # ---- conditions evaluated at send time
        if kind in ("briefing", "bfu", "checkin", "cfu") and not daymode.is_active(s, d):
            r.status = "cancelled"
            return
        if kind == "warning" and not self.cfg.warn_on_off_days and not daymode.is_active(s, now.date()):
            r.status = "cancelled"
            return
        if kind == "briefing":
            if self._run(s, d).briefing_sent_at:
                r.status = "cancelled"
                return
        if kind == "bfu":
            run = self._run(s, d)
            if not run.briefing_sent_at or (st.last_inbound_at and st.last_inbound_at >= run.briefing_sent_at):
                r.status = "cancelled"
                return
        if kind == "checkin" and self._run(s, d).checkin_prompted_at:
            r.status = "cancelled"
            return
        if kind == "cfu":
            run = self._run(s, d)
            conv = self._conv(s)
            confirming = (conv.state == "confirming" and conv.updated_at is not None
                          and now - conv.updated_at < timedelta(hours=2))
            if not run.checkin_prompted_at or run.checkin_answered_at or confirming:
                r.status = "cancelled"
                return
        if kind == "reminder":
            rem = s.get(Reminder, r.ref_id)
            if not rem or rem.status != "active":
                r.status = "cancelled"
                return
        if kind == "meeting":
            m = s.get(Meeting, r.ref_id)
            if not m or m.status != "active":
                r.status = "cancelled"
                return
        expired = r.expires_at is not None and now > r.expires_at
        if expired and kind not in ("reminder", "meeting"):
            r.status = "expired"
            return
        text = r.text
        if expired:  # a missed reminder/meeting: still tell the user, but honestly
            text = f"Na-miss mo ({fmt_day(r.due_at)} {fmt_time(r.due_at)}): " + self._plain(r)
        # ---- window gate
        if not self.window_ok(s, now):
            if kind in ("reminder", "meeting", "briefing", "checkin"):
                r.status = "pending"
                if r.urgent and email_enabled(self.cfg):
                    send_email(self.cfg, "Urgent reminder", self._plain(r))
            else:
                r.status = "expired"
            return
        qr = r.quick_replies
        if kind == "briefing":
            text = briefing.build_briefing(s, d)
        elif kind == "checkin":
            ok = self._send_checkin(s, d, now)
            r.status, r.sent_at = ("sent", now) if ok else ("pending", None)
            return
        elif kind == "warning":
            text, qr = self._warning_text(s, now)
        for i, c in enumerate(_clip(text)):
            self.channel.send(self.psid, c, qr if i == len(_clip(text)) - 1 else None)
        self._log(s, "out", text)
        r.status, r.sent_at = "sent", now
        if kind == "briefing":
            self._run(s, d).briefing_sent_at = now
        elif kind == "reminder":
            rem = s.get(Reminder, r.ref_id)
            if rem:
                rem.status = "sent"
