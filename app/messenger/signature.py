from __future__ import annotations

import hashlib
import hmac


def sign(app_secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """Validate X-Hub-Signature-256. Always constant-time; empty secret/header never validates."""
    if not app_secret or not header:
        return False
    return hmac.compare_digest(sign(app_secret, body), header.strip())
