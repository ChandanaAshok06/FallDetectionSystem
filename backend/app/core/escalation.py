"""Server-side escalation of alerts nobody has acknowledged.

Runs on the server, so it still works if the wearable's battery dies or its
Wi-Fi drops (the firmware cannot escalate anything itself).

An alert still "active" after ESCALATION_TIMEOUT_S (default 120 s) is set to
"escalated" and the caregiver is notified again. A wearer button press or a
caregiver acknowledgment still works on an escalated alert.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import List

from app.core import notify
from app.db.models import Alert, Device
from app.db.session import SessionLocal

log = logging.getLogger("fall.escalation")


def _timeout_s() -> float:
    try:
        return float(os.getenv("ESCALATION_TIMEOUT_S", "120"))
    except ValueError:
        return 120.0


def escalate_overdue(now: datetime | None = None) -> List[str]:
    """Escalate overdue active alerts, once each. Returns the escalated alert ids."""
    now = now or datetime.utcnow()
    cutoff = now - timedelta(seconds=_timeout_s())
    done: List[str] = []
    db = SessionLocal()
    try:
        overdue = db.query(Alert).filter(Alert.status == "active", Alert.created_at <= cutoff).all()
        for alert in overdue:
            alert.status = "escalated"
            db.commit()                    # mark first, so an alert can only ever be escalated once
            device = db.query(Device).filter(Device.id == alert.device_id).first()
            if device is None:
                continue
            text = notify.escalation_message(device, alert.id)
            notify.create_notification(db, device=device, alert_id=alert.id,
                                       kind="escalation", message=text)
            notify.deliver_external(text, subject="NO RESPONSE - possible fall",
                                    email_to=notify.caregiver_email_for(db, device))
            done.append(alert.id)
    finally:
        db.close()
    return done


async def escalation_loop(interval_s: float = 10.0) -> None:
    while True:
        try:
            await asyncio.to_thread(escalate_overdue)
        except Exception as exc:
            log.error("Escalation check failed: %s", type(exc).__name__)
        await asyncio.sleep(interval_s)
