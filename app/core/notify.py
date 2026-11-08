"""Optional urgent e-mail fallback (SMTP). Disabled unless SMTP_HOST and SMTP_TO are set."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)


def email_enabled(settings) -> bool:  # noqa: ANN001
    return bool(settings.smtp_host and settings.smtp_to)


def send_email(settings, subject: str, body: str) -> bool:  # noqa: ANN001
    if not email_enabled(settings):
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, settings.smtp_user or settings.smtp_to, settings.smtp_to
    msg.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("email failed: %s", e)
        return False
