from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

# quick reply = {"title": "...", "payload": "..."}
QuickReplies = list[dict]


@dataclass
class InboundEvent:
    """A normalized user action. message / quick_reply / reaction all open the 24h window."""

    kind: str  # message | quick_reply | reaction | postback
    psid: str
    text: str = ""
    payload: str | None = None
    mid: str | None = None
    ts: datetime | None = None
    raw: dict = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        if self.mid:
            return f"{self.kind}:{self.mid}"
        return f"{self.kind}:{self.psid}:{self.ts.isoformat() if self.ts else ''}:{self.payload or self.text}"


class Channel(Protocol):
    def send(self, psid: str, text: str, quick_replies: QuickReplies | None = None) -> None: ...

    def send_file(self, psid: str, path: str, caption: str = "") -> None: ...


class RecordingChannel:
    """Collects outbound messages in memory (tests, simulations, console)."""

    def __init__(self, echo: bool = False, clock=None):  # noqa: ANN001
        self.sent: list[dict] = []
        self.files: list[dict] = []
        self.echo = echo
        self.clock = clock
        self.fail = False

    def send(self, psid, text, quick_replies=None):  # noqa: ANN001
        if self.fail:
            raise RuntimeError("send failed")
        at = self.clock.now() if self.clock else None
        self.sent.append({"text": text, "quick_replies": quick_replies, "at": at})
        if self.echo:
            qr = f"   [{' | '.join(q['title'] for q in quick_replies)}]" if quick_replies else ""
            stamp = f"[{at:%a %b %d %H:%M}] " if at else ""
            print(f"\n🤖 {stamp}{text}{qr}")

    def send_file(self, psid, path, caption=""):  # noqa: ANN001
        self.files.append({"path": path, "caption": caption})
        if self.echo:
            print(f"\n🤖 📎 {path}  {caption}")

    def texts(self) -> list[str]:
        return [m["text"] for m in self.sent]
