"""Caregiver notifications.

NOTE: this file was REBUILT. An earlier version of app/core/notify.py was
overwritten by mistake and could not be recovered, so this one provides the API
that predict.py / alerts.py / main.py already import:

    Notification, create_notification, deliver_external,
    fall_alert_message, wearer_responded_message

Every notification is (1) stored in the `notifications` table (visible to the
caregiver at GET /notifications), (2) written to the server log, and (3) sent on
each configured external channel. Channels are optional and set by environment
variables:

  Telegram : TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  Email    : SMTP_HOST, SMTP_FROM, [SMTP_PORT=587] [SMTP_USER] [SMTP_PASS] [SMTP_SSL=0]
             (sent to the caregiver's account email)
  Display  : NOTIFY_TZ_OFFSET_MIN (default 330 = IST)

External delivery runs on a background thread, so a slow Telegram/SMTP call can
never hold up /predict or the device's acknowledgment request (the ESP32 gives
the acknowledge request only 5 s). It never raises, and it never logs request
URLs because the Telegram URL contains the bot token.
"""
import logging
import os
import smtplib
import threading
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Optional

import requests
from sqlalchemy import Column, DateTime, String, Text

from app.db.session import Base

log = logging.getLogger("fall.notify")


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), nullable=False)
    device_id = Column(String(36), nullable=False)
    alert_id = Column(String(36), nullable=True)
    kind = Column(String(30), nullable=False)    # fall_alert | wearer_responded | escalation
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------- messages
def _fmt(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.utcnow()                      # naive UTC, like the DB
    try:
        offset = int(os.getenv("NOTIFY_TZ_OFFSET_MIN", "330"))
    except ValueError:
        offset = 330
    local = dt.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(minutes=offset)))
    sign = "+" if offset >= 0 else "-"
    hh, mm = divmod(abs(offset), 60)
    return local.strftime("%d %b %Y %H:%M:%S") + f" (UTC{sign}{hh:02d}:{mm:02d})"


def fall_alert_message(device, alert_id: str) -> str:
    return (
        f"POSSIBLE FALL detected for {device.device_label} at {_fmt()}.\n"
        f"Alert {str(alert_id)[:8]}. The wearer has not responded yet. Please check on them now."
    )


def wearer_responded_message(device, alert_id: str) -> str:
    return (
        f"UPDATE: {device.device_label} pressed the \"I'm OK\" button at {_fmt()}.\n"
        f"This responds to possible-fall alert {str(alert_id)[:8]}. "
        "The wearer reports being fine. Please still confirm if you are concerned."
    )


def escalation_message(device, alert_id: str) -> str:
    return (
        f"NO RESPONSE: possible-fall alert {str(alert_id)[:8]} for {device.device_label} "
        "has not been acknowledged by the wearer. Please check on them immediately or call for help."
    )


# ---------------------------------------------------------------- storage
def create_notification(db, *, device, alert_id: Optional[str], kind: str, message: str) -> Notification:
    n = Notification(user_id=device.user_id, device_id=device.id,
                     alert_id=alert_id, kind=kind, message=message)
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


def caregiver_email_for(db, device) -> Optional[str]:
    from app.db.models import User
    user = db.query(User).filter(User.id == device.user_id).first()
    return user.caregiver_email if user else None


# ---------------------------------------------------------------- delivery
def _send_telegram(text: str) -> bool:
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": text}, timeout=8)
        if r.status_code == 200:
            return True
        log.error("Telegram notification failed: HTTP %s", r.status_code)
    except Exception as exc:       # str(exc) can contain the URL, i.e. the token
        log.error("Telegram notification failed: %s", type(exc).__name__)
    return False


def _send_email(to_addr: Optional[str], subject: str, text: str) -> bool:
    host, sender = os.getenv("SMTP_HOST"), os.getenv("SMTP_FROM")
    if not host or not sender or not to_addr:
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to_addr, subject
    msg.set_content(text)
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
        use_ssl = os.getenv("SMTP_SSL", "0") in ("1", "true", "True")
        smtp = smtplib.SMTP_SSL(host, port, timeout=10) if use_ssl else smtplib.SMTP(host, port, timeout=10)
        with smtp:
            if not use_ssl:
                smtp.starttls()
            user, pw = os.getenv("SMTP_USER"), os.getenv("SMTP_PASS")
            if user and pw:
                smtp.login(user, pw)
            smtp.send_message(msg)
        return True
    except Exception as exc:
        log.error("Email notification failed: %s", type(exc).__name__)
        return False


def _deliver(text: str, subject: str, email_to: Optional[str]) -> None:
    try:
        log.warning("NOTIFY %s | %s", subject, text.replace("\n", " "))
        _send_telegram(f"{subject}\n\n{text}")
        _send_email(email_to, subject, text)
    except Exception as exc:
        log.error("Notification failed: %s", type(exc).__name__)


def deliver_external(text: str, subject: str = "Fall detection alert",
                     email_to: Optional[str] = None) -> threading.Thread:
    """Fire-and-forget delivery on a background thread. Returns the thread (tests may join it)."""
    t = threading.Thread(target=_deliver, args=(text, subject, email_to), daemon=True)
    t.start()
    return t
