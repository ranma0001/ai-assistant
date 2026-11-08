import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.core.channel import RecordingChannel
from app.harness import Harness
from app.main import create_app
from app.messenger.events import parse_webhook
from app.messenger.signature import sign, verify_signature
from conftest import dt

SECRET = "shh"


def page_payload(text="hello", psid="u1", mid="mid.1", **extra):
    msg = {"mid": mid, "text": text, **extra}
    return {"object": "page", "entry": [{"messaging": [{"sender": {"id": psid}, "timestamp": 1_700_000_000_000,
                                                          "message": msg}]}]}


def test_signature():
    body = b'{"a":1}'
    assert verify_signature(SECRET, body, sign(SECRET, body))
    assert not verify_signature(SECRET, body, sign("other", body))
    assert not verify_signature(SECRET, body, None)
    assert not verify_signature("", body, sign("", body))


def test_parse_events():
    evs = parse_webhook(page_payload("hi"))
    assert evs[0].kind == "message" and evs[0].text == "hi"
    qr = parse_webhook(page_payload("👍 Tuloy", quick_reply={"payload": "KEEPALIVE"}))
    assert qr[0].kind == "quick_reply" and qr[0].payload == "KEEPALIVE"
    echo = parse_webhook(page_payload("x", is_echo=True))
    assert echo == []
    react = {"object": "page", "entry": [{"messaging": [{"sender": {"id": "u1"}, "timestamp": 1,
                                                          "reaction": {"mid": "m", "action": "react"}}]}]}
    assert parse_webhook(react)[0].kind == "reaction"
    unreact = {"object": "page", "entry": [{"messaging": [{"sender": {"id": "u1"}, "timestamp": 1,
                                                            "reaction": {"mid": "m", "action": "unreact"}}]}]}
    assert parse_webhook(unreact) == []


def make_client():
    cfg = Settings(_env_file=None, parser="rule", owner_psid="u1", fb_app_secret=SECRET, fb_verify_token="vt",
                   channel="console", log_dir="logs_test")
    h = Harness(dt(11, 10, 10, 0), settings=cfg)
    return TestClient(create_app(cfg, h.bot)), h


def post(client, payload, secret=SECRET):
    body = json.dumps(payload).encode()
    return client.post("/webhook", content=body, headers={"x-hub-signature-256": sign(secret, body),
                                                            "content-type": "application/json"})


def test_webhook_verify_and_health():
    c, _ = make_client()
    assert c.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "vt",
                                     "hub.challenge": "123"}).text == "123"
    assert c.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "no",
                                     "hub.challenge": "123"}).status_code == 403
    assert c.get("/health").json()["status"] == "ok"


def test_webhook_rejects_bad_signature():
    c, h = make_client()
    r = post(c, page_payload(), secret="wrong")
    assert r.status_code == 403
    assert h.texts() == []


def test_webhook_processes_and_dedups():
    c, h = make_client()
    p = page_payload("tapos ko na yung backup", mid="mid.42")
    assert post(c, p).status_code == 200
    n = len(h.texts())
    assert n >= 1 and "Ito ang naintindihan ko" in h.texts()[-1]
    post(c, p)  # Meta redelivers the same event
    assert len(h.texts()) == n


def test_webhook_ignores_other_senders():
    c, h = make_client()
    assert post(c, page_payload("hi", psid="stranger")).status_code == 200
    assert h.texts() == []


def test_attachment_only_like_is_not_text():
    evs = parse_webhook({"object": "page", "entry": [{"messaging": [{"sender": {"id": "u1"}, "timestamp": 1,
                         "message": {"mid": "m9", "attachments": [{"type": "image"}]}}]}]})
    assert len(evs) == 1
