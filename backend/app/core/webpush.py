"""Web push delivery: a caregiver's browser or phone gets the alert even when the app is closed.

The frontend already registers a service worker (public/sw.js) and saves each browser's push
subscription through POST /push/subscribe. This module is the missing half: it sends to every
subscription of the caregiver who owns the device.

Configuration (environment variables; push is silently skipped if either is missing):

  VAPID_PRIVATE_KEY_FILE : path to the VAPID private key PEM written by
                           tools/generate_vapid_keys.py (relative paths are resolved from the
                           backend folder)       -- or --
  VAPID_PRIVATE_KEY      : the same key as a base64url string
  VAPID_SUBJECT          : "mailto:you@your-domain.com" or an https:// URL that identifies the
                           sender. Use a real address: some push services (Apple's) reject
                           placeholder subjects.
  WEBPUSH_TTL_S          : how long a push service may hold a message for an offline phone
                           (default 600 s; an old fall alert is not worth delivering hours late)

The matching PUBLIC key goes into the frontend as VITE_VAPID_PUBLIC_KEY. Browsers bind a
subscription to that public key, so after changing the key pair every caregiver must turn
notifications on again.

Sending never raises and never logs the subscription endpoint (it is a secret URL).
"""
import json
import logging
import os
from pathlib import Path
from typing import Optional

from app.db.models import PushSubscription
from app.db.session import SessionLocal

log = logging.getLogger("fall.webpush")

BACKEND_ROOT = Path(__file__).resolve().parents[2]
MAX_BODY_CHARS = 240


class SubscriptionExpired(Exception):
    """The push service says this subscription no longer exists (HTTP 404 or 410)."""


# ---------------------------------------------------------------- configuration
def _private_key() -> Optional[str]:
    path = os.getenv("VAPID_PRIVATE_KEY_FILE", "").strip()
    if path:
        p = Path(path)
        if not p.is_absolute() and not p.exists():
            p = BACKEND_ROOT / p
        return str(p) if p.is_file() else None
    return os.getenv("VAPID_PRIVATE_KEY", "").strip() or None


def _subject() -> Optional[str]:
    s = os.getenv("VAPID_SUBJECT", "").strip()
    return s if s.startswith(("mailto:", "https://")) else None


def _ttl_s() -> int:
    try:
        return max(0, int(os.getenv("WEBPUSH_TTL_S", "600")))
    except ValueError:
        return 600


def is_configured() -> bool:
    return bool(_private_key() and _subject())


# ---------------------------------------------------------------- sending
def _send(subscription_info: dict, payload: str, private_key: str, subject: str, ttl: int) -> None:
    """One encrypted push. Raises SubscriptionExpired for 404/410; other errors propagate."""
    from pywebpush import WebPushException, webpush      # imported here so the app runs without it

    try:
        webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=private_key,
            vapid_claims={"sub": subject},               # fresh dict: pywebpush adds fields to it
            ttl=ttl,
            headers={"Urgency": "high"},                 # wake a sleeping phone for a fall alert
            timeout=8,
        )
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            raise SubscriptionExpired() from None
        raise RuntimeError(f"push service answered HTTP {status}") from None


def send_to_user(user_id: str, title: str, body: str, url: str = "/alerts") -> int:
    """Push to every saved subscription of this caregiver. Returns how many were accepted."""
    key, subject = _private_key(), _subject()
    if not key or not subject:
        return 0

    payload = json.dumps({"title": title, "body": " ".join(body.split())[:MAX_BODY_CHARS], "url": url})
    sent = 0
    db = SessionLocal()
    try:
        subs = db.query(PushSubscription).filter(PushSubscription.user_id == user_id).all()
        for sub in subs:
            info = {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}
            try:
                _send(info, payload, key, subject, _ttl_s())
                sent += 1
            except SubscriptionExpired:
                db.delete(sub)                           # the browser unsubscribed or was reset
                db.commit()
            except Exception as exc:                     # one bad subscription must not stop the rest
                log.error("Web push failed: %s", exc if isinstance(exc, RuntimeError) else type(exc).__name__)
    except Exception as exc:
        log.error("Web push failed: %s", type(exc).__name__)
    finally:
        db.close()
    return sent
