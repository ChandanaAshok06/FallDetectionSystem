from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import numpy as np
from app.models.predict import PredictRequest, PredictResponse
from app.inference.ensemble import run_ensemble, THRESHOLD
from app.db.session import get_db
from app.db.models import Prediction, Alert, Device
from app.core.deps import get_current_device
from app.core.decision import window_features, evaluate_window
from app.continual import serving as personal_serving, store as personal_store
from app.core.notify import (
    caregiver_email_for, create_notification, deliver_external, fall_alert_message,
)

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
    p_global = run_ensemble(window)
    # Global ensemble only, until this wearer's personal model exists (continual learning);
    # afterwards global + personal. Falls back to the global ensemble on any problem.
    probability, model_tag, p_personal = personal_serving.blend(device.id, window, p_global)
    label = "fall" if probability >= THRESHOLD else "adl"

    prediction = Prediction(
        device_id=device.id,
        label=label,
        probability=probability,
        threshold_used=THRESHOLD,
        model_used=model_tag,
    )
    db.add(prediction)
    db.commit()
    db.refresh(prediction)

    # The model's raw call (label/probability above) is stored unchanged. An ALERT is
    # only raised when the decision layer confirms impact followed by no recovery.
    features = window_features(window)
    decision = evaluate_window(device.id, probability, THRESHOLD, features, window=window)

    alert_id = None
    if decision.action == "confirmed":
        alert = Alert(prediction_id=prediction.id, device_id=device.id)
        db.add(alert)
        db.commit()
        db.refresh(alert)
        alert_id = alert.id
        # Keep the impact window: when the caregiver verifies this alert it becomes a labelled sample.
        personal_store.save_alert_window(device.id, alert.id, decision.window)
        text = fall_alert_message(device, alert.id)
        create_notification(db, device=device, alert_id=alert.id,
                            kind="fall_alert", message=text)
        deliver_external(text, subject="Possible fall detected",
                         email_to=caregiver_email_for(db, device))

    return PredictResponse(
        prediction_id=prediction.id,
        label=label,
        probability=probability,
        threshold_used=THRESHOLD,
        model_used=model_tag,
        alert_id=alert_id,
        p_global=p_global,
        p_personal=p_personal,
        decision=decision.text,
        peak_svm_g=features["peak_svm_g"],
    )