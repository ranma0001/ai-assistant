from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

IntentName = Literal[
    "set_reminder", "add_meeting", "add_task", "log_activity", "checkin_answer", "checkin_skip",
    "set_day_mode", "complete_task", "cancel_or_edit", "snooze", "query_agenda",
    "generate_report", "unknown",
]


class Entry(BaseModel):
    raw: str = Field(description="Exact words copied from the user's message")
    status: Literal["done", "ongoing", "not_done"] = "done"


class Intent(BaseModel):
    intent: IntentName
    confidence: float = 0.5
    title: str | None = None
    when_text: str | None = Field(None, description="time expression exactly as written")
    duration_text: str | None = None
    due_text: str | None = None
    date_text: str | None = None
    range_text: str | None = None
    mode: Literal["work", "aral", "off", "leave"] | None = None
    attendees: list[str] = Field(default_factory=list)
    entries: list[Entry] = Field(default_factory=list)
    task_ref: str | None = None
    target_ref: str | None = None
    action: Literal["cancel", "edit"] | None = None


class FormalEntry(BaseModel):
    id: int
    text: str


class FormalReport(BaseModel):
    items: list[FormalEntry]
