from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.models import Alert, Device, User
from app.models.alert import AcknowledgeRequest, VerifyRequest
from app.core.deps import get_current_user

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
    return alert