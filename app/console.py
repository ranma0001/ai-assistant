"""Terminal chat with the assistant - no Facebook needed.

  python -m app.console                 # real clock, persistent data/console.db
  python -m app.console --sim           # fake clock + in-memory DB (time-travel)

Commands:  /time                show the (fake) clock
           /goto 2026-11-14 13:30   jump the fake clock, running every scheduled job on the way (--sim)
           /+90m  /+21h  /+2d       advance the fake clock
           /tap PAYLOAD             press a quick-reply button, e.g. /tap KEEPALIVE  or  /tap CONFIRM
           /react                   send a 👍 reaction
           /state                   window / mode / pending summary
           /quit
"""
from __future__ import annotations

import argparse
import sys
import re
import threading
import time
from datetime import datetime, timedelta

from app.config import Settings
from app.harness import Harness
from app.timeutil import TZ, Clock


def main() -> None:
    for stream in (sys.stdout, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true", help="fake clock, in-memory DB")
    ap.add_argument("--start", help="sim start, e.g. '2026-11-13 16:20'")
    ap.add_argument("--db", default="sqlite:///data/console.db")
    a = ap.parse_args()

    cfg = Settings(_env_file=".env", owner_psid="console-user")
    if a.sim:
        start = datetime.strptime(a.start, "%Y-%m-%d %H:%M").replace(tzinfo=TZ) if a.start else \
            datetime.now(TZ).replace(second=0, microsecond=0)
        h = Harness(start, settings=cfg, echo=True)
        print(f"SIM mode. Clock = {h.clock.now():%a %Y-%m-%d %H:%M}. Type /help-ish commands: see module docstring.")
    else:
        from app.llm.parsers import build_parser
        from app.core.assistant import Assistant
        from app.core.channel import RecordingChannel
        from app.db.session import make_session_factory

        class Real(Harness):
            def __init__(self):  # noqa: D107
                self.cfg = cfg
                self.clock = Clock()
                self.channel = RecordingChannel(echo=True, clock=self.clock)
                self.sessions = make_session_factory(a.db)
                self.bot = Assistant(cfg, self.sessions, self.channel, self.clock, build_parser(cfg))
                self._n = 0

        h = Real()
        print(h.bot.startup())
        stop = threading.Event()

        def loop() -> None:
            while not stop.wait(15):
                h.bot.tick()

        threading.Thread(target=loop, daemon=True).start()

    while True:
        try:
            line = input("\n🧑 > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line == "/quit":
            break
        if line == "/time":
            print(h.clock.now().strftime("%a %Y-%m-%d %H:%M"))
        elif line == "/state":
            print(h.bot.startup() if not a.sim else _state(h))
        elif line == "/react":
            h.react()
        elif line.startswith("/tap "):
            h.tap(line[5:].strip())
        elif line.startswith("/goto ") and a.sim:
            h.advance_to(datetime.strptime(line[6:].strip(), "%Y-%m-%d %H:%M").replace(tzinfo=TZ), step_min=1)
        elif a.sim and (m := re.fullmatch(r"/\+(\d+)([mhd])", line)):
            n, u = int(m.group(1)), m.group(2)
            h.advance_to(h.clock.now() + timedelta(**{{"m": "minutes", "h": "hours", "d": "days"}[u]: n}))
        else:
            h.say(line)
        if not a.sim:
            time.sleep(0.05)


def _state(h: Harness) -> str:
    return h.bot.startup()


if __name__ == "__main__":
    main()
