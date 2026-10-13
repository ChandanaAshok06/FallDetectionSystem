"""Prediction: global ensemble until a personal model exists, then global + personal.

Never raises: if anything about the personal model fails (missing file, load error, bad shape),
the window is scored by the global ensemble alone, exactly as before.
"""
import logging
import threading
from typing import Dict, Optional, Tuple

import joblib
import numpy as np

from app.continual import store

log = logging.getLogger("fall.continual")

_lock = threading.Lock()
_models: Dict[str, Tuple[int, object]] = {}      # device_id -> (version, keras model)
_scaler = None


def _get_scaler():
    global _scaler
    if _scaler is None:
        _scaler = joblib.load(store.MODELS_DIR / "standard_scaler.pkl")
    return _scaler


def _get_model(device_id: str, version: int, path: str):
    with _lock:
        hit = _models.get(device_id)
        if hit and hit[0] == version:
            return hit[1]
        from tensorflow.keras.models import load_model
        model = load_model(path)
        _models[device_id] = (version, model)
        return model


def blend(device_id: str, window: np.ndarray, p_global: float) -> Tuple[float, str, Optional[float]]:
    """Return (final_probability, model_tag, personal_probability_or_None)."""
    try:
        info = store.active_model_info(device_id)
        if info is None:
            return p_global, "global_ensemble", None
        version, path = info
        model = _get_model(device_id, version, path)
        flat = np.asarray(window, dtype=float).reshape(-1, 6)
        x = _get_scaler().transform(flat).reshape(1, 512, 6)
        p_personal = float(model.predict(x, verbose=0)[0][0])
        if store.cfg.mode == "replace":
            return p_personal, f"personal_v{version}", p_personal
        w = store.blend_weight(store._active_n.get(device_id, 0))
        return (1.0 - w) * p_global + w * p_personal, f"blend_v{version}", p_personal
    except Exception as exc:
        log.error("Personal model unavailable, using global ensemble: %s", type(exc).__name__)
        return p_global, "global_ensemble", None
