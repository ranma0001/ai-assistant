"""Turn a Messenger webhook payload into normalized InboundEvents."""
from __future__ import annotations

import logging
from datetime import datetime

from app.core.channel import InboundEvent
from app.timeutil import TZ

log = logging.getLogger(__name__)


def parse_webhook(payload: dict) -> list[InboundEvent]:
    out: list[InboundEvent] = []
    if payload.get("object") != "page":
        return out
    for entry in payload.get("entry", []):
        for m in entry.get("messaging", []):
            psid = (m.get("sender") or {}).get("id")
            if not psid:
                continue
            ts = datetime.fromtimestamp(m["timestamp"] / 1000, TZ) if m.get("timestamp") else None
            if "message" in m:
                msg = m["message"]
                if msg.get("is_echo"):
                    continue  # our own outbound - must not count as inbound
                qr = (msg.get("quick_reply") or {}).get("payload")
                text = msg.get("text") or ""
                if qr:
                    out.append(InboundEvent("quick_reply", psid, text=text, payload=qr, mid=msg.get("mid"),
                                            ts=ts, raw=m))
                elif text:
                    out.append(InboundEvent("message", psid, text=text, mid=msg.get("mid"), ts=ts, raw=m))
                elif msg.get("attachments"):
                    # stickers/likes count as a user action; treat the thumbs-up sticker as a keep-alive
                    out.append(InboundEvent("message", psid, text="", payload="KEEPALIVE", mid=msg.get("mid"),
                                            ts=ts, raw=m))
            elif "postback" in m:
                pb = m["postback"]
                out.append(InboundEvent("postback", psid, text=pb.get("title", ""), payload=pb.get("payload"),
                                        mid=pb.get("mid"), ts=ts, raw=m))
            elif "reaction" in m:
                r = m["reaction"]
                if r.get("action") == "react":
                    out.append(InboundEvent("reaction", psid, payload="react", mid=None, ts=ts, raw=m))
    return out
