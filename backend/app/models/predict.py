from pydantic import BaseModel, field_validator
from typing import List
from uuid import UUID
from datetime import datetime

class PredictRequest(BaseModel):
    device_id: UUID
    window_start: datetime
    sample_rate_hz: int
    data: List[List[float]]

    @field_validator("data")
    @classmethod
    def check_window_shape(cls, v):
        if len(v) != 512:
            raise ValueError(f"expected 512 rows, got {len(v)}")
        for row in v:
            if len(row) != 6:
                raise ValueError(f"expected 6 values per row, got {len(row)}")
        return v

class PredictResponse(BaseModel):
    prediction_id: UUID
    label: str
    probability: float
    threshold_used: float
    model_used: str
    alert_id: UUID | None
    decision: str | None = None
    peak_svm_g: float | None = None
    p_global: float | None = None      # global ensemble probability
    p_personal: float | None = None    # personal model probability (None until one is live)