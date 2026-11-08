from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.timeutil import TZ


class ManilaDT(TypeDecorator):
    """Stores naive Manila-local datetimes; always returns tz-aware (Asia/Manila)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed")
        return value.astimezone(TZ).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=TZ)


class Base(DeclarativeBase):
    pass


class UserState(Base):
    __tablename__ = "user_state"
    psid: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_inbound_at: Mapped[datetime | None] = mapped_column(ManilaDT, nullable=True)
    last_inbound_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    prefs: Mapped[dict] = mapped_column(JSON, default=dict)


class InboxEvent(Base):
    __tablename__ = "inbox_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    dedup_key: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    received_at: Mapped[datetime] = mapped_column(ManilaDT)
    processed_at: Mapped[datetime | None] = mapped_column(ManilaDT, nullable=True)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    direction: Mapped[str] = mapped_column(String(8))
    text: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(40), nullable=True)
    parse: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ts: Mapped[datetime] = mapped_column(ManilaDT)


class ConvState(Base):
    __tablename__ = "conversation_state"
    psid: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(40), default="idle")
    draft: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime | None] = mapped_column(ManilaDT, nullable=True)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    due_at: Mapped[datetime | None] = mapped_column(ManilaDT, nullable=True)
    due_granularity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="open")  # open|done|dropped
    context: Mapped[str] = mapped_column(String(12), default="work")
    created_at: Mapped[datetime] = mapped_column(ManilaDT)
    done_at: Mapped[datetime | None] = mapped_column(ManilaDT, nullable=True)


class Reminder(Base):
    __tablename__ = "reminders"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    remind_at: Mapped[datetime] = mapped_column(ManilaDT)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active|sent|cancelled
    created_at: Mapped[datetime] = mapped_column(ManilaDT)


class Meeting(Base):
    __tablename__ = "meetings"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    start_at: Mapped[datetime] = mapped_column(ManilaDT)
    end_at: Mapped[datetime | None] = mapped_column(ManilaDT, nullable=True)
    attendees: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(12), default="chat")
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="active")


class LogEntry(Base):
    __tablename__ = "log_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    raw_text: Mapped[str] = mapped_column(Text)  # verbatim words of the user - never edited
    summary: Mapped[str] = mapped_column(Text, default="")
    log_date: Mapped[date] = mapped_column(Date)
    context: Mapped[str] = mapped_column(String(12), default="work")  # work|school
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(12), default="adhoc")  # checkin|adhoc
    ipcr_tag: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(ManilaDT)


class DayMode(Base):
    __tablename__ = "day_modes"
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    mode: Mapped[str] = mapped_column(String(12))  # work|aral|off|leave|holiday
    source: Mapped[str] = mapped_column(String(12), default="user")


class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))
    text: Mapped[str] = mapped_column(Text, default="")
    quick_replies: Mapped[list | None] = mapped_column(JSON, nullable=True)
    due_at: Mapped[datetime] = mapped_column(ManilaDT)
    expires_at: Mapped[datetime | None] = mapped_column(ManilaDT, nullable=True)
    dedup_key: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True)
    # scheduled|sent|pending|expired|cancelled|failed
    status: Mapped[str] = mapped_column(String(12), default="scheduled")
    urgent: Mapped[bool] = mapped_column(Boolean, default=False)
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ref_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(ManilaDT, nullable=True)
    created_at: Mapped[datetime] = mapped_column(ManilaDT)


class DailyRun(Base):
    __tablename__ = "daily_runs"
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    briefing_sent_at: Mapped[datetime | None] = mapped_column(ManilaDT, nullable=True)
    checkin_prompted_at: Mapped[datetime | None] = mapped_column(ManilaDT, nullable=True)
    checkin_answered_at: Mapped[datetime | None] = mapped_column(ManilaDT, nullable=True)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    file_path: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(ManilaDT)
