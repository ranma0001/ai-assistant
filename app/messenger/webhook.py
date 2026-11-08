from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response

from app.core.assistant import Assistant
from app.config import Settings
from app.messenger.events import parse_webhook
from app.messenger.signature import verify_signature

log = logging.getLogger(__name__)


def make_router(settings: Settings, bot: Assistant) -> APIRouter:
    router = APIRouter()

    @router.get("/webhook")
    def verify(mode: str = Query("", alias="hub.mode"), token: str = Query("", alias="hub.verify_token"),
               challenge: str = Query("", alias="hub.challenge")):
        if mode == "subscribe" and settings.fb_verify_token and token == settings.fb_verify_token:
            return Response(content=challenge, media_type="text/plain")
        raise HTTPException(403, "verification failed")

    def process(events) -> None:  # noqa: ANN001
        for ev in events:
            try:
                bot.on_inbound(ev)
            except Exception:  # noqa: BLE001
                log.exception("failed processing event %s", ev.dedup_key)

    @router.post("/webhook")
    async def receive(request: Request, background: BackgroundTasks):
        body = await request.body()
        if not verify_signature(settings.fb_app_secret, body, request.headers.get("x-hub-signature-256")):
            log.warning("rejected webhook with bad signature")
            raise HTTPException(403, "bad signature")
        try:
            payload = json.loads(body)
        except ValueError:
            raise HTTPException(400, "bad json") from None
        events = []
        for ev in parse_webhook(payload):
            if settings.owner_psid in ("", "console-user"):
                log.warning("OWNER_PSID is not set. Message came from PSID %s - put it in .env as OWNER_PSID.",
                            ev.psid)
                continue
            if ev.psid != settings.owner_psid:
                log.warning("ignoring message from non-owner PSID %s", ev.psid)
                continue
            events.append(ev)
        if events:
            background.add_task(process, events)  # ack immediately, process after the response
        return {"status": "ok"}

    return router
