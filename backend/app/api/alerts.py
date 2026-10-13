from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.models import Alert, Device, User
from app.models.alert import AcknowledgeRequest, VerifyRequest
from app.core.deps import get_current_user, get_current_device
from app.continual import store as personal_store
from app.core.notify import (
    Notification, caregiver_email_for, create_notification, deliver_external,
    wearer_responded_message,
)

router = APIRouter()

def _get_owned_alert(alert_id: str, db: Session, current_user: User) -> Alert:
    """Shared helper: fetch an alert and verify the current user owns its device."""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    device = db.query(Device).filter(Device.id == alert.device_id).first()
    if device is None or device.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You do not own this alert")

    return alert

@router.get("/alerts")
def list_alerts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(Alert)
        .join(Device, Alert.device_id == Device.id)
        .filter(Device.user_id == current_user.id)
        .order_by(Alert.created_at.desc())
        .all()
    )

@router.get("/notifications")
def list_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(Notification)
        .filter(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(100)
        .all()
    )

@router.get("/alerts/{alert_id}")
def get_alert(
    alert_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_owned_alert(alert_id, db, current_user)

@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(
    alert_id: str,
    payload: AcknowledgeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    alert = _get_owned_alert(alert_id, db, current_user)

    alert.status = "acknowledged"
    alert.acknowledged_at = datetime.utcnow()
    alert.acknowledged_by = payload.acknowledged_by
    db.commit()
    db.refresh(alert)
    return alert

@router.post("/alerts/{alert_id}/device-acknowledge")
def device_acknowledge_alert(
    alert_id: str,
    db: Session = Depends(get_db),
    device: Device = Depends(get_current_device),
):
    """Wearer pressed the button on the device itself.

    Authenticated with X-Device-Key (not a caregiver JWT) and scoped so a device
    can only acknowledge its own alerts. Idempotent: if the alert is no longer
    "active" (already acknowledged, or verified by a caregiver) it is returned
    unchanged with 200, so the firmware can safely retry without overwriting
    anything. A button press means "the wearer responded", not "the wearer is
    safe" -- the caregiver still decides via /verify.
    """
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.device_id != device.id:
        raise HTTPException(
            status_code=403,
            detail="This alert does not belong to the authenticated device",
        )

    # "escalated" (no response within the timeout) can still be answered by the wearer.
    if alert.status in ("active", "escalated"):
        alert.status = "acknowledged"
        alert.acknowledged_at = datetime.utcnow()
        alert.acknowledged_by = "wearer (device button)"
        db.commit()
        db.refresh(alert)

        # Second caregiver notification: the wearer responded. Only on the
        # active/escalated -> acknowledged transition, so firmware retries cannot spam.
        text = wearer_responded_message(device, alert.id)
        create_notification(db, device=device, alert_id=alert.id,
                            kind="wearer_responded", message=text)
        deliver_external(text, subject="Wearer pressed the I'm OK button",
                         email_to=caregiver_email_for(db, device))

    return {
        "alert_id": alert.id,
        "status": alert.status,
        "acknowledged_at": alert.acknowledged_at.isoformat() if alert.acknowledged_at else None,
        "acknowledged_by": alert.acknowledged_by,
    }

@router.post("/alerts/{alert_id}/verify")
def verify_alert(
    alert_id: str,
    payload: VerifyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    alert = _get_owned_alert(alert_id, db, current_user)

    alert.status = payload.verified_label
    alert.verified_label = payload.verified_label
    db.commit()
    db.refresh(alert)

    # Continual learning: the verified alert becomes a labelled training sample for this wearer's
    # personal model, and the retraining rules are checked. Never allowed to break verification.
    personal_store.on_alert_verified(db, alert)
    return alert