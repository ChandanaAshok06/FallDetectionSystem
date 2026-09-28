from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.models import User, Device
from app.core.security import decode_access_token

bearer_scheme = HTTPBearer()

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials  # HTTPBearer already strips "Bearer " for us
    user_id = decode_access_token(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    return user


def get_current_device(x_device_key: str = Header(None), db: Session = Depends(get_db)) -> Device:
    if x_device_key is None:
        raise HTTPException(status_code=401, detail="Missing X-Device-Key header")

    device = db.query(Device).filter(Device.device_key == x_device_key).first()
    if device is None:
        raise HTTPException(status_code=401, detail="Invalid device key")

    return device