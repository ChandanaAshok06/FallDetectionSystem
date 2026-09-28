from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.models import Prediction, Device, User
from app.core.deps import get_current_user

router = APIRouter()

@router.get("/predictions")
def list_predictions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(Prediction)
        .join(Device, Prediction.device_id == Device.id)
        .filter(Device.user_id == current_user.id)
        .order_by(Prediction.created_at.desc())
        .all()
    )

@router.get("/predictions/{prediction_id}")
def get_prediction(
    prediction_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    prediction = db.query(Prediction).filter(Prediction.id == prediction_id).first()
    if prediction is None:
        raise HTTPException(status_code=404, detail="Prediction not found")

    device = db.query(Device).filter(Device.id == prediction.device_id).first()
    if device is None or device.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You do not own this prediction")

    return prediction