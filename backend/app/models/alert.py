from pydantic import BaseModel
from typing import Literal

class AcknowledgeRequest(BaseModel):
    acknowledged_by: str

class VerifyRequest(BaseModel):
    verified_label: Literal["fall", "false_alarm"]