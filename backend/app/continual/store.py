"""Personalization / continual-learning bookkeeping (no TensorFlow imported here).

Lifecycle for one wearer (device):

  1. Until a personal model exists, every window is scored by the GLOBAL ensemble only.
  2. When the caregiver verifies an alert (fall / false_alarm) the alert's IMPACT window is
     stored as a labelled sample (1 = fall, 0 = not a fall).
  3. Once CL_MIN_SAMPLES verified samples exist (and at least CL_MIN_PER_CLASS of each class),
     ONE personal model is trained (a fine-tuned copy of a global model; see train.py).
  4. After that, every CL_RETRAIN_EVERY NEW verified samples trigger a retrain on ALL verified
     samples so far. Each retrain creates a new version; a version only goes live if it passes the
     promotion guard in train.py, otherwise the previous version keeps serving.

Training always runs in a separate process, so it can never stall /predict.

Environment variables (defaults in brackets):
  CL_MIN_SAMPLES [50]      verified samples before the first personal model
  CL_MIN_PER_CLASS [3]     minimum verified falls AND minimum verified non-falls for that
  CL_RETRAIN_EVERY [10]    new verified samples between retrains
  CL_TRAIN_INLINE [0]      1 = train inside the request (tests only)
"""
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
from sqlalchemy import Column, DateTime, Integer, String, Text, func

from app.db import session as dbsession
from app.db.session import Base

log = logging.getLogger("fall.continual")

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BACKEND_DIR / "data" / "personal"
MODELS_DIR = BACKEND_DIR / "models_store"
PERSONAL_MODELS_DIR = MODELS_DIR / "personal"
RUNNING_STALE_S = 3600          # a "running" row older than this is treated as a crashed trainer


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


class Config:
    """Read from the environment on every access, so changes and tests take effect immediately."""
    @property
    def min_samples(self): return _i("CL_MIN_SAMPLES", 50)
    @property
    def min_per_class(self): return _i("CL_MIN_PER_CLASS", 3)
    @property
    def retrain_every(self): return _i("CL_RETRAIN_EVERY", 10)
    @property
    def mode(self): return os.getenv("CL_MODE", "blend")          # blend | replace
    @property
    def blend_mode(self): return os.getenv("CL_BLEND_MODE", "adaptive")   # adaptive (notebook 17) | fixed
    @property
    def blend_max(self): return min(1.0, max(0.0, _f("CL_BLEND_MAX", 0.5)))
    @property
    def blend_w(self): return min(1.0, max(0.0, _f("CL_BLEND_W", 0.5)))
    @property
    def base_model(self): return os.getenv("CL_BASE_MODEL", "CNN_BiLSTM_Final.keras")
    @property
    def epochs(self): return _i("CL_EPOCHS", 30)
    @property
    def min_steps(self): return _i("CL_MIN_STEPS", 0)       # floor on gradient steps, whatever the sample count
    @property
    def augment_factor(self): return _i("CL_AUGMENT", 1)
    @property
    def lr(self): return _f("CL_LR", 1e-4)
    @property
    def unfreeze_lstm(self): return os.getenv("CL_UNFREEZE_LSTM", "0") in ("1", "true", "True")
    @property
    def train_inline(self): return os.getenv("CL_TRAIN_INLINE", "0") in ("1", "true", "True")


cfg = Config()


def blend_weight(n_samples: int) -> float:
    """Weight of the personal model. Matches notebook 17: w = MAX * n / (n + 200), MAX = 0.5.
    CL_BLEND_MODE=fixed uses CL_BLEND_W instead. Models only serve after passing the guard, so the
    notebook's 'halve if F1 got worse' rule is covered by the guard."""
    if cfg.blend_mode == "fixed":
        return cfg.blend_w
    n = max(0, int(n_samples or 0))
    return cfg.blend_max * n / (n + 200.0)


# ------------------------------------------------------------------ tables
class ContinualSample(Base):
    __tablename__ = "cl_samples"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String(36), nullable=False, index=True)
    alert_id = Column(String(36), nullable=False, unique=True)
    label = Column(Integer, nullable=False)          # 1 = verified fall, 0 = verified false alarm
    window_path = Column(String(300), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class ContinualModel(Base):
    __tablename__ = "cl_models"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String(36), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    status = Column(String(12), nullable=False)       # running | active | superseded | rejected | failed
    n_samples = Column(Integer, default=0)
    n_pos = Column(Integer, default=0)
    n_neg = Column(Integer, default=0)
    model_path = Column(String(300), nullable=True)
    metrics = Column(Text, nullable=True)             # JSON: guard results, errors
    created_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)


# ------------------------------------------------------------------ window files
def window_path(device_id: str, alert_id: str) -> Path:
    return DATA_DIR / device_id / "windows" / f"{alert_id}.npy"


def save_alert_window(device_id: str, alert_id: str, window) -> bool:
    """Keep the impact window of a raised alert so a later verification can label it."""
    if window is None:
        return False
    try:
        p = window_path(device_id, alert_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        np.save(p, np.asarray(window, dtype=np.float32))
        return True
    except Exception as exc:
        log.error("Could not save alert window: %s", type(exc).__name__)
        return False


# ------------------------------------------------------------------ counting
def counts(db, device_id: str) -> Dict[str, int]:
    rows = (db.query(ContinualSample.label, func.count())
            .filter(ContinualSample.device_id == device_id)
            .group_by(ContinualSample.label).all())
    pos = next((n for l, n in rows if l == 1), 0)
    neg = next((n for l, n in rows if l == 0), 0)
    return {"total": pos + neg, "pos": pos, "neg": neg}


def _latest_finished(db, device_id: str) -> Optional[ContinualModel]:
    return (db.query(ContinualModel)
            .filter(ContinualModel.device_id == device_id,
                    ContinualModel.status.in_(("active", "superseded", "rejected")))
            .order_by(ContinualModel.version.desc()).first())


def _active(db, device_id: str) -> Optional[ContinualModel]:
    return (db.query(ContinualModel)
            .filter(ContinualModel.device_id == device_id, ContinualModel.status == "active")
            .order_by(ContinualModel.version.desc()).first())


# ------------------------------------------------------------------ serving lookup (TTL cached)
_active_cache: Dict[str, Tuple[float, Optional[Tuple[int, str]]]] = {}
_active_n: Dict[str, int] = {}
ACTIVE_TTL_S = 5.0


def active_model_info(device_id: str) -> Optional[Tuple[int, str]]:
    """(version, path) of the model currently serving this device, or None (= global ensemble)."""
    now = time.time()
    hit = _active_cache.get(device_id)
    if hit and now - hit[0] < ACTIVE_TTL_S:
        return hit[1]
    db = dbsession.SessionLocal()
    try:
        row = _active(db, device_id)
        info = (row.version, row.model_path) if row and row.model_path else None
        _active_n[device_id] = int(row.n_samples or 0) if row else 0
    finally:
        db.close()
    _active_cache[device_id] = (now, info)
    return info


def invalidate_cache(device_id: Optional[str] = None) -> None:
    if device_id is None:
        _active_cache.clear()
    else:
        _active_cache.pop(device_id, None)


# ------------------------------------------------------------------ triggering
def on_alert_verified(db, alert) -> Dict:
    """Called after a caregiver verifies an alert. Records the labelled sample, then checks the
    retraining rules. Never raises."""
    try:
        if alert.verified_label not in ("fall", "false_alarm"):
            return {"recorded": False, "reason": "no verified label"}
        label = 1 if alert.verified_label == "fall" else 0
        path = window_path(alert.device_id, alert.id)
        if not path.exists():
            log.warning("No stored window for alert %s (raised before continual learning was "
                        "enabled?); verification not used as a training sample.", alert.id[:8])
            return {"recorded": False, "reason": "no stored window for this alert"}

        sample = db.query(ContinualSample).filter(ContinualSample.alert_id == alert.id).first()
        if sample is None:
            db.add(ContinualSample(device_id=alert.device_id, alert_id=alert.id,
                                   label=label, window_path=str(path)))
        else:
            sample.label = label                     # caregiver changed their mind
            sample.updated_at = datetime.utcnow()
        db.commit()
        result = maybe_trigger_training(db, alert.device_id)
        result["recorded"] = True
        return result
    except Exception as exc:
        log.error("Continual-learning hook failed: %s", type(exc).__name__)
        try:
            db.rollback()
        except Exception:
            pass
        return {"recorded": False, "reason": "error"}


def should_train(db, device_id: str) -> Tuple[bool, str]:
    c = counts(db, device_id)
    last = _latest_finished(db, device_id)
    if last is None:
        if c["total"] < cfg.min_samples:
            return False, f"waiting for first model: {c['total']}/{cfg.min_samples} verified samples"
        if c["pos"] < cfg.min_per_class or c["neg"] < cfg.min_per_class:
            return False, (f"need at least {cfg.min_per_class} verified falls and {cfg.min_per_class} "
                           f"false alarms (have {c['pos']} / {c['neg']})")
        return True, "initial personal model"
    new = c["total"] - (last.n_samples or 0)
    if new < cfg.retrain_every:
        return False, f"{new}/{cfg.retrain_every} new verified samples since last training"
    if c["pos"] < 1 or c["neg"] < 1:
        return False, "need both classes"
    return True, f"retrain: {new} new verified samples"


def maybe_trigger_training(db, device_id: str, force: bool = False) -> Dict:
    # one training at a time per device
    running = (db.query(ContinualModel)
               .filter(ContinualModel.device_id == device_id, ContinualModel.status == "running").first())
    if running is not None:
        age = (datetime.utcnow() - running.created_at).total_seconds()
        if age < RUNNING_STALE_S:
            return {"started": False, "reason": "training already running"}
        running.status, running.finished_at = "failed", datetime.utcnow()
        running.metrics = json.dumps({"error": "stale running row (trainer crashed or was killed)"})
        db.commit()

    if force:
        c = counts(db, device_id)
        ok, why = (c["pos"] >= 1 and c["neg"] >= 1), "manual training"
        if not ok:
            return {"started": False, "reason": "need at least one verified fall and one false alarm"}
    else:
        ok, why = should_train(db, device_id)
    if not ok:
        return {"started": False, "reason": why}

    c = counts(db, device_id)
    top = db.query(func.max(ContinualModel.version)).filter(ContinualModel.device_id == device_id).scalar() or 0
    row = ContinualModel(device_id=device_id, version=top + 1, status="running",
                         n_samples=c["total"], n_pos=c["pos"], n_neg=c["neg"])
    db.add(row)
    db.commit()
    db.refresh(row)
    row_id, version = row.id, row.version

    if cfg.train_inline:
        from app.continual.train import train_for_row
        train_for_row(row_id)
    else:
        _spawn(row_id, device_id, version)
    return {"started": True, "reason": why, "version": version}


def _spawn(row_id: str, device_id: str, version: int) -> None:
    try:
        log_dir = DATA_DIR / device_id
        log_dir.mkdir(parents=True, exist_ok=True)
        out = open(log_dir / f"train_v{version}.log", "ab")
        subprocess.Popen(
            [sys.executable, "-m", "app.continual.train", "--model-id", row_id],
            cwd=str(BACKEND_DIR),            # the SQLite path in app/db/session.py is cwd-relative
            stdout=out, stderr=subprocess.STDOUT, env=os.environ.copy(),
        )
    except Exception as exc:
        log.error("Could not start trainer: %s", type(exc).__name__)
        db = dbsession.SessionLocal()
        try:
            r = db.query(ContinualModel).filter(ContinualModel.id == row_id).first()
            if r:
                r.status, r.finished_at = "failed", datetime.utcnow()
                r.metrics = json.dumps({"error": "could not start trainer process"})
                db.commit()
        finally:
            db.close()


# ------------------------------------------------------------------ status for the API
def status(db, device_id: str) -> Dict:
    c = counts(db, device_id)
    active = _active(db, device_id)
    last = _latest_finished(db, device_id)
    running = (db.query(ContinualModel)
               .filter(ContinualModel.device_id == device_id, ContinualModel.status == "running").first())
    ok, why = should_train(db, device_id)
    if last is None:
        remaining = max(cfg.min_samples - c["total"], 0)
    else:
        remaining = max(cfg.retrain_every - (c["total"] - (last.n_samples or 0)), 0)
    return {
        "device_id": device_id,
        "serving": (f"{'personal' if cfg.mode == 'replace' else 'blend'}_v{active.version}"
                    if active else "global_ensemble"),
        "verified_samples": c,
        "settings": {"min_samples": cfg.min_samples, "min_per_class": cfg.min_per_class,
                     "retrain_every": cfg.retrain_every, "mode": cfg.mode, "blend_w": cfg.blend_w,
                     "base_model": cfg.base_model},
        "more_verified_samples_needed": remaining,
        "training_running": running is not None,
        "training_next": why,
        "last_training": None if last is None else {
            "version": last.version, "status": last.status, "n_samples": last.n_samples,
            "finished_at": last.finished_at.isoformat() if last.finished_at else None,
            "metrics": json.loads(last.metrics) if last.metrics else None},
    }
