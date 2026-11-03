from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StrictFloat
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.models import User
from app.core.deps import get_current_user
from app.inference.ensemble import THRESHOLD

router = APIRouter()


class ThresholdUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # StrictFloat rejects bools and numeric strings; allow_inf_nan=False rejects NaN/Infinity;
    # ge/le enforce the range 0.15 to 0.6 (inclusive).
    threshold: StrictFloat = Field(
        ge=0.15,
        le=0.6,
        allow_inf_nan=False,
)


class ThresholdResponse(BaseModel):
    personalized_threshold: Optional[float]
    global_threshold: float
    effective_threshold: float
    source: str  # "personalized" or "global"


def _threshold_view(user: User) -> ThresholdResponse:
    personalized = user.personalized_threshold
    if personalized is not None:
        effective, source = personalized, "personalized"
    else:
        effective, source = THRESHOLD, "global"
    return ThresholdResponse(
        personalized_threshold=personalized,
        global_threshold=THRESHOLD,
        effective_threshold=effective,
        source=source,
    )


# The target user is always the authenticated one; there is no user_id in the path or body.
@router.get("/users/me/threshold", response_model=ThresholdResponse)
def get_my_threshold(current_user: User = Depends(get_current_user)):
    return _threshold_view(current_user)


@router.put("/users/me/threshold", response_model=ThresholdResponse)
def set_my_threshold(
    payload: ThresholdUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    current_user.personalized_threshold = payload.threshold
    db.commit()
    db.refresh(current_user)
    return _threshold_view(current_user)


@router.delete("/users/me/threshold", response_model=ThresholdResponse)
def reset_my_threshold(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    current_user.personalized_threshold = None
    db.commit()
    db.refresh(current_user)
    return _threshold_view(current_user)
