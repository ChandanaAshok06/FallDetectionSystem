"""Device pairing: a device gets its device_id and device_key without copy and paste.

1. Device: POST /devices/pair {code}                 -> poll_token (kept in the device's memory)
2. User:   POST /devices/claim {code, device_label}  -> creates the device (needs login)
3. Device: POST /devices/pair/credentials {code, poll_token}
           -> 202 while waiting; 200 with device_id + device_key exactly once after the claim.

A pairing request lives 10 minutes and is single-use. Rate limits are kept in memory
(single uvicorn process) and reset on restart.

Known limitation: if the device loses power after receiving its credentials but before saving
them, it cannot fetch them again (410). Delete the device in the app and pair again.
"""
import hashlib
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.security import generate_device_key
from app.db.models import Device, PairingRequest, User
from app.db.session import get_db

router = APIRouter()

PAIRING_TTL_S = 600
POLL_INTERVAL_S = 3
CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{4,18}[A-Z0-9]$")   # 6 to 20 characters

_hits = defaultdict(deque)
_lock = threading.Lock()


def _allow(key: str, limit: int, window_s: int) -> bool:
    now = time.time()
    with _lock:
        q = _hits[key]
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def reset_limits() -> None:
    with _lock:
        _hits.clear()


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clean_code(raw: str) -> str:
    code = (raw or "").strip().upper().replace(" ", "")
    if not CODE_RE.match(code):
        raise HTTPException(status_code=400, detail="Invalid pairing code format")
    return code


class PairIn(BaseModel):
    code: str


class ClaimIn(BaseModel):
    code: str
    device_label: str


class PollIn(BaseModel):
    code: str
    poll_token: str


@router.post("/devices/pair")
def pair_announce(payload: PairIn, request: Request, db: Session = Depends(get_db)):
    """Called by the device (no login). Returns a secret poll_token for this device only."""
    if not _allow(f"announce:{_ip(request)}", 20, 60):
        raise HTTPException(status_code=429, detail="Too many requests")
    code = _clean_code(payload.code)
    now = datetime.utcnow()

    db.query(PairingRequest).filter(PairingRequest.expires_at < now).delete(synchronize_session=False)
    existing = db.query(PairingRequest).filter(PairingRequest.code == code).first()
    if existing is not None:
        if existing.status == "claimed":
            raise HTTPException(status_code=409, detail="This code was just claimed; wait for it to expire")
        db.delete(existing)   # a waiting request (device restarted) or a finished one is replaced

    token = secrets.token_urlsafe(24)
    db.add(PairingRequest(
        code=code,
        poll_token_hash=_hash(token),
        status="waiting",
        expires_at=now + timedelta(seconds=PAIRING_TTL_S),
    ))
    db.commit()
    return {
        "poll_token": token,
        "expires_in_seconds": PAIRING_TTL_S,
        "poll_interval_seconds": POLL_INTERVAL_S,
    }


@router.post("/devices/claim")
def claim_device(
    payload: ClaimIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Called by the logged-in user with the code printed on the device."""
    if not _allow(f"claim:{current_user.id}", 10, 600):
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")
    code = _clean_code(payload.code)
    label = (payload.device_label or "").strip()
    if not label or len(label) > 100:
        raise HTTPException(status_code=400, detail="Device label must be 1 to 100 characters")

    now = datetime.utcnow()
    pairing = (
        db.query(PairingRequest)
        .filter(
            PairingRequest.code == code,
            PairingRequest.status == "waiting",
            PairingRequest.expires_at >= now,
        )
        .first()
    )
    if pairing is None:
        raise HTTPException(
            status_code=404,
            detail="Code not found or expired. Make sure the device is in pairing mode.",
        )

    device = Device(user_id=current_user.id, device_label=label, device_key=generate_device_key())
    db.add(device)
    db.flush()   # gives the device its id
    pairing.device_id = device.id
    pairing.status = "claimed"
    pairing.expires_at = now + timedelta(seconds=PAIRING_TTL_S)   # time for the device to collect it
    db.commit()
    # The key is NOT returned here: only the device receives it.
    return {"device_id": device.id, "device_label": label}


@router.post("/devices/pair/credentials")
def pair_credentials(
    payload: PollIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Polled by the device. Returns the credentials exactly once, after the user claimed the code."""
    if not _allow(f"poll:{_ip(request)}", 60, 60):
        raise HTTPException(status_code=429, detail="Too many requests")
    code = _clean_code(payload.code)

    pairing = db.query(PairingRequest).filter(PairingRequest.code == code).first()
    if pairing is None or not secrets.compare_digest(pairing.poll_token_hash, _hash(payload.poll_token)):
        raise HTTPException(status_code=404, detail="Unknown pairing request")
    if pairing.expires_at < datetime.utcnow() or pairing.status == "delivered":
        raise HTTPException(status_code=410, detail="Pairing request expired or already completed")
    if pairing.status == "waiting":
        response.status_code = 202
        return {"status": "waiting"}

    device = db.query(Device).filter(Device.id == pairing.device_id).first()
    if device is None:
        raise HTTPException(status_code=410, detail="Device no longer exists")
    pairing.status = "delivered"
    db.commit()
    return {"status": "claimed", "device_id": device.id, "device_key": device.device_key}