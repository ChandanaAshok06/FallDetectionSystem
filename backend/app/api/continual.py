from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.continual import store
from app.core.deps import get_current_user
from app.db.models import Device, User
from app.db.session import get_db

router = APIRouter()


def _owned_device(device_id: str, db: Session, user: User) -> Device:
    device = db.query(Device).filter(Device.id == device_id).first()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if device.user_id != user.id:
        raise HTTPException(status_code=403, detail="You do not own this device")
    return device


@router.get("/personalization/{device_id}/status")
def personalization_status(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """How many verified samples exist, which model is serving, and when the next training happens."""
    _owned_device(device_id, db, current_user)
    return store.status(db, device_id)


@router.post("/personalization/{device_id}/train")
def personalization_train(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually start a training run now (needs at least one verified fall and one false alarm)."""
    _owned_device(device_id, db, current_user)
    result = store.maybe_trigger_training(db, device_id, force=True)
    if not result["started"]:
        raise HTTPException(status_code=409, detail=result["reason"])
    return result
