"""FastAPI app: webhook + health + background scheduler tick.  Run: uvicorn app.main:app --port 8000"""
from __future__ import annotations

import logging
import logging.handlers
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.core.assistant import Assistant
from app.core.channel import RecordingChannel
from app.db.session import make_session_factory
from app.llm.parsers import LLMParser, build_parser
from app.messenger.client import MessengerChannel
from app.messenger.webhook import make_router
from app.timeutil import Clock

log = logging.getLogger("app")
TICK_SECONDS = 20


def setup_logging(settings: Settings) -> None:
    Path(settings.log_dir).mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(Path(settings.log_dir) / "assistant.log", maxBytes=2_000_000,
                                              backupCount=5, encoding="utf-8")
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        root.addHandler(fh)
        root.addHandler(sh)


def build_assistant(settings: Settings) -> Assistant:
    sessions = make_session_factory(settings.db_url)
    parser = build_parser(settings)
    formalizer = None
    if settings.llm_base_url and settings.parser != "rule":
        formalizer = LLMParser(settings.llm_base_url, settings.llm_api_key, settings.llm_report_model,
                               settings.llm_timeout_s * 4)
    if settings.channel == "messenger":
        channel = MessengerChannel(settings.fb_page_token, settings.fb_graph_version)
    else:
        channel = RecordingChannel(echo=True)
    return Assistant(settings, sessions, channel, Clock(), parser, formalizer)


def create_app(settings: Settings | None = None, bot: Assistant | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings)
    bot = bot or build_assistant(settings)
    stop = threading.Event()

    def ticker() -> None:
        while not stop.wait(TICK_SECONDS):
            try:
                bot.tick()
            except Exception:  # noqa: BLE001
                log.exception("tick failed")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        banner = bot.startup()  # missed-job sweep happens here
        log.info("STARTED env=%s channel=%s | %s", settings.app_env, settings.channel, banner)
        print(banner)
        t = threading.Thread(target=ticker, daemon=True, name="ticker")
        t.start()
        yield
        stop.set()

    app = FastAPI(title="Personal AI Assistant", lifespan=lifespan)
    app.state.bot = bot

    @app.get("/health")
    def health():
        with bot.sessions() as s:
            ok = bot.window_ok(s, bot.clock.now())
            st = bot._state(s)
            last = st.last_inbound_at.isoformat() if st.last_inbound_at else None
        return {"status": "ok", "env": settings.app_env, "channel": settings.channel,
                "now": bot.clock.now().isoformat(), "window_open": ok, "last_inbound_at": last}

    app.include_router(make_router(settings, bot))
    return app


def __getattr__(name: str):  # lazy: `uvicorn app.main:app`
    if name == "app":
        return create_app()
    raise AttributeError(name)
