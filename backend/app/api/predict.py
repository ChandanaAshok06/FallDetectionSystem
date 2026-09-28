from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import numpy as np
from app.models.predict import PredictRequest, PredictResponse
from app.inference.ensemble import run_ensemble, THRESHOLD
from app.db.session import get_db
from app.db.models import Prediction, Alert, Device
from app.core.deps import get_current_device

router = APIRouter()

@router.post("/predict", response_model=PredictResponse)
def predict(
    payload: PredictRequest,
    db: Session = Depends(get_db),
    device: Device = Depends(get_current_device),
):
    if str(payload.device_id) != device.id:
        raise HTTPException(
            status_code=403,
            detail="device_id in request body does not match the authenticated device",
        )

    window = np.array(payload.data)
    probability = run_ensemble(window)
    label = "fall" if probability >= THRESHOLD else "adl"

    prediction = Prediction(
        device_id=device.id,
        label=label,
        probability=probability,
        threshold_used=THRESHOLD,
        model_used="global_ensemble",
    )
    db.add(prediction)
    db.commit()
    db.refresh(prediction)

    alert_id = None
    if label == "fall":
        alert = Alert(prediction_id=prediction.id, device_id=device.id)
        db.add(alert)
        db.commit()
        db.refresh(alert)
        alert_id = alert.id

    return PredictResponse(
        prediction_id=prediction.id,
        label=label,
        probability=probability,
        threshold_used=THRESHOLD,
        model_used="global_ensemble",
        alert_id=alert_id,
    )