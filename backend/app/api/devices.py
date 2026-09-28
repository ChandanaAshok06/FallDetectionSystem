from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.models import Device, User
from app.core.deps import get_current_user
from app.core.security import generate_device_key
from app.models.device import DeviceResponse

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