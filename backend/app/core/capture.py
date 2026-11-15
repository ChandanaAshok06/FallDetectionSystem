"""Debug-only labeled window capture.

While enabled, every window the wearable POSTs to /predict is also written to
    <CAPTURE_DIR>/<activity>/capture_00001.csv   (same CSV layout as the SD card)
and one row is appended to <CAPTURE_DIR>/<activity>/index.csv with the model's
probability, the gate decision and the peak acceleration.

The activity is chosen at runtime on the /capture page, so you can switch from
"standing" to "walking" without re-flashing anything.

Enable with:   CAPTURE_ENABLED=1   (off by default; the /capture routes then 404)
Optional:      CAPTURE_DIR=captures            where files go (relative to the folder uvicorn runs in)
               CAPTURE_SKIP_AFTER_CHANGE=1     windows dropped right after changing activity,
                                               so the moment you get into position is not saved
"""
import csv
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ENABLED = os.getenv("CAPTURE_ENABLED", "0") == "1"
CAPTURE_DIR = Path(os.getenv("CAPTURE_DIR", "captures"))
SKIP_AFTER_CHANGE = int(os.getenv("CAPTURE_SKIP_AFTER_CHANGE", "1"))

LABEL_RE = re.compile(r"^[a-z0-9_]{1,30}$")
PRESETS = ["standing", "walking", "sit_stand", "lying", "pickup", "fall_mattress"]
HEADER = "acc_x,acc_y,acc_z,gyro_x,gyro_y,gyro_z"
INDEX_COLUMNS = ["file", "saved_utc", "window_start", "device_id", "peak_svm_g",
                 "probability", "model_label", "decision", "mean_x", "mean_y", "mean_z"]

_lock = threading.Lock()
_state = {"label": None, "skip": 0, "next": {}, "last": "nothing saved yet"}


def _folder(label: str) -> Path:
    return CAPTURE_DIR / label


def _disk_count(label: str) -> int:
    d = _folder(label)
    return len(list(d.glob("capture_*.csv"))) if d.is_dir() else 0


def _next_index(label: str) -> int:
    if label not in _state["next"]:
        top = 0
        d = _folder(label)
        if d.is_dir():
            for p in d.glob("capture_*.csv"):
                m = re.match(r"capture_(\d+)\.csv$", p.name)
                if m:
                    top = max(top, int(m.group(1)))
        _state["next"][label] = top + 1
    return _state["next"][label]


def set_label(label):
    """label=None turns saving off. Raises ValueError on a bad name."""
    with _lock:
        if label is None:
            _state["label"] = None
            _state["last"] = "capture off"
            return
        if not LABEL_RE.match(label):
            raise ValueError("use 1-30 characters: lowercase letters, digits, underscore")
        _folder(label).mkdir(parents=True, exist_ok=True)
        _state["label"] = label
        _state["skip"] = SKIP_AFTER_CHANGE
        _state["last"] = f"waiting: next {SKIP_AFTER_CHANGE} window(s) are skipped while you get into position"


def status():
    with _lock:
        labels = list(PRESETS)
        if _state["label"] and _state["label"] not in labels:
            labels.append(_state["label"])
        return {
            "enabled": ENABLED,
            "label": _state["label"],
            "last": _state["last"],
            "folder": str(CAPTURE_DIR.resolve()),
            "counts": {l: _disk_count(l) for l in labels},
            "presets": PRESETS,
        }


def save_window(device_id, window, window_start, probability, model_label, decision_text, peak_svm_g):
    """Called from /predict. Never raises into the caller's flow if wrapped in try/except there."""
    if not ENABLED:
        return None
    with _lock:
        label = _state["label"]
        if label is None:
            return None
        if _state["skip"] > 0:
            _state["skip"] -= 1
            _state["last"] = f"skipped one settling window ({label}); {_state['skip']} more to skip"
            return None

        d = _folder(label)
        d.mkdir(parents=True, exist_ok=True)
        idx = _next_index(label)
        name = f"capture_{idx:05d}.csv"
        arr = np.asarray(window, dtype=float)
        np.savetxt(d / name, arr, delimiter=",", header=HEADER, comments="", fmt="%.2f")
        _state["next"][label] = idx + 1

        index_path = d / "index.csv"
        new_file = not index_path.exists()
        with open(index_path, "a", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(INDEX_COLUMNS)
            w.writerow([
                name,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                getattr(window_start, "isoformat", lambda: str(window_start))(),
                device_id,
                f"{peak_svm_g:.3f}",
                f"{probability:.4f}",
                model_label,
                decision_text,
                f"{arr[:, 0].mean():.1f}", f"{arr[:, 1].mean():.1f}", f"{arr[:, 2].mean():.1f}",
            ])
        _state["last"] = f"saved {label}/{name}"
        return d / name
