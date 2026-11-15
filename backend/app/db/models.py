import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, DateTime
from app.db.session import Base

def gen_uuid():
    return str(uuid.uuid4())

class Prediction(Base):
    __tablename__ = "predictions"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    device_id = Column(String(36), nullable=False)
    label = Column(String(10), nullable=False)
    probability = Column(Float, nullable=False)
    threshold_used = Column(Float, nullable=False)
    model_used = Column(String(30), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Alert(Base):
    __tablename__ = "alerts"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    prediction_id = Column(String(36), nullable=False)
    device_id = Column(String(36), nullable=False)
    status = Column(String(20), default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    acknowledged_at = Column(DateTime, nullable=True)
    acknowledged_by = Column(String(100), nullable=True)   # NEW
    verified_label = Column(String(20), nullable=True)      # NEW

class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    name = Column(String(100), nullable=False)
    caregiver_email = Column(String(255), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    personalized_threshold = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Device(Base):
    __tablename__ = "devices"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), nullable=False)
    device_label = Column(String(100), nullable=False)
    device_key = Column(String(64), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)



class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), nullable=False)
    endpoint = Column(String(2048), unique=True, nullable=False)
    p256dh = Column(String(255), nullable=False)
    auth = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class PairingRequest(Base):
    """A device waiting to be claimed (or just claimed) through the pairing flow."""
    __tablename__ = "pairing_requests"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    code = Column(String(20), nullable=False, index=True)
    poll_token_hash = Column(String(64), nullable=False)
    status = Column(String(12), nullable=False, default="waiting")  # waiting | claimed | delivered
    device_id = Column(String(36), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
