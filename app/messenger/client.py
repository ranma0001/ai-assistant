"""Graph API sender. UNTESTED against live Meta - see docs/USER_TODO.md for the verification checklist."""
from __future__ import annotations

import json
import logging
import mimetypes
from pathlib import Path

import httpx

from app.core.channel import QuickReplies

log = logging.getLogger(__name__)


class MessengerChannel:
    def __init__(self, page_token: str, graph_version: str = "v21.0", timeout: float = 20.0):
        self.token = page_token
        self.base = f"https://graph.facebook.com/{graph_version}/me/messages"
        self.timeout = timeout

    def _post(self, **kw) -> dict:
        r = httpx.post(self.base, params={"access_token": self.token}, timeout=self.timeout, **kw)
        if r.status_code >= 400:
            log.error("Graph API %s: %s", r.status_code, r.text[:500])
            r.raise_for_status()
        return r.json()

    def send(self, psid: str, text: str, quick_replies: QuickReplies | None = None) -> None:
        message: dict = {"text": text[:2000]}
        if quick_replies:
            message["quick_replies"] = [
                {"content_type": "text", "title": q["title"][:20], "payload": q["payload"]}
                for q in quick_replies[:13]
            ]
        # RESPONSE = inside the standard 24h window. Tags are never used (deprecated / not for automation).
        self._post(json={"recipient": {"id": psid}, "messaging_type": "RESPONSE", "message": message})

    def send_file(self, psid: str, path: str, caption: str = "") -> None:
        p = Path(path)
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        data = {
            "recipient": json.dumps({"id": psid}),
            "message": json.dumps({"attachment": {"type": "file", "payload": {"is_reusable": False}}}),
            "messaging_type": "RESPONSE",
        }
        with p.open("rb") as fh:
            self._post(data=data, files={"filedata": (p.name, fh, mime)})
