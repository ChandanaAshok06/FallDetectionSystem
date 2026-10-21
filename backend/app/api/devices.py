from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.models import Device, User
from app.core.deps import get_current_user
from app.core.security import generate_device_key
from app.models.device import DeviceResponse

from typing import List
from app.db.models import Device, User, Alert, Prediction

router = APIRouter()

@router.post("/devices/register")
def register_device(
    device_label: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    device = Device(
        user_id=current_user.id,
        device_label=device_label,
        device_key=generate_device_key(),
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    # The ONLY place device_key is ever returned
    return {"device_id": device.id, "device_key": device.device_key}

@router.get("/devices/{device_id}", response_model=DeviceResponse)
def get_device(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    device = db.query(Device).filter(Device.id == device_id).first()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    if device.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You do not own this device")

    return device

@router.get("/devices", response_model=List[DeviceResponse])
def list_devices(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(Device)
        .filter(Device.user_id == current_user.id)
        .order_by(Device.created_at.desc())
        .all()
    )


@router.delete("/devices/{device_id}", status_code=204)
def delete_device(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    device = db.query(Device).filter(Device.id == device_id).first()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if device.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You do not own this device")

    db.query(Alert).filter(Alert.device_id == device_id).delete(synchronize_session=False)
    db.query(Prediction).filter(Prediction.device_id == device_id).delete(synchronize_session=False)
    db.delete(device)
    db.commit()