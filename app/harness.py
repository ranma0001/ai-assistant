"""Builds a fully wired Assistant with a fake clock and in-memory DB. Used by tests and the console."""
from __future__ import annotations

from datetime import datetime, timedelta

from app.config import Settings
from app.core.assistant import Assistant
from app.core.channel import InboundEvent, RecordingChannel
from app.db.session import make_session_factory
from app.llm.parsers import CompositeParser
from app.timeutil import FakeClock, ensure_aware


class Harness:
    def __init__(self, start: datetime, settings: Settings | None = None, parser=None,  # noqa: ANN001
                 db_url: str = "sqlite:///:memory:", echo: bool = False, formalizer=None):  # noqa: ANN001
        self.cfg = settings or Settings(_env_file=None, parser="rule", owner_psid="u1")
        self.clock = FakeClock(start)
        self.channel = RecordingChannel(echo=echo, clock=self.clock)
        self.sessions = make_session_factory(db_url)
        self.bot = Assistant(self.cfg, self.sessions, self.channel, self.clock,
                             parser or CompositeParser(None), formalizer)
        self._n = 0

    # ---- user actions
    def _ev(self, kind: str, **kw) -> InboundEvent:
        self._n += 1
        return InboundEvent(kind=kind, psid=self.cfg.owner_psid, mid=f"m{self._n}", ts=self.clock.now(), **kw)

    def say(self, text: str) -> list[str]:
        n = len(self.channel.sent)
        self.bot.on_inbound(self._ev("message", text=text))
        return self.channel.texts()[n:]

    def tap(self, payload: str, title: str = "") -> list[str]:
        n = len(self.channel.sent)
        self.bot.on_inbound(self._ev("quick_reply", text=title, payload=payload))
        return self.channel.texts()[n:]

    def react(self) -> list[str]:
        n = len(self.channel.sent)
        self.bot.on_inbound(self._ev("reaction", payload="react"))
        return self.channel.texts()[n:]

    # ---- time
    def advance_to(self, when: datetime, step_min: int = 1) -> None:
        when = ensure_aware(when)
        step = timedelta(minutes=step_min)
        while self.clock.now() < when:
            nxt = min(self.clock.now() + step, when)
            self.clock.set(nxt)
            self.bot.tick()

    def advance(self, **kw) -> None:
        self.advance_to(self.clock.now() + timedelta(**kw))

    # ---- inspection
    def texts(self) -> list[str]:
        return self.channel.texts()

    def sent_at(self, contains: str) -> list[datetime]:
        return [m["at"] for m in self.channel.sent if contains in m["text"]]

    def last(self) -> str:
        return self.channel.sent[-1]["text"] if self.channel.sent else ""
