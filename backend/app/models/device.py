from pydantic import BaseModel
from datetime import datetime

class DeviceResponse(BaseModel):
    id: str
    user_id: str
    device_label: str
    created_at: datetime

    class Config:
        from_attributes = True