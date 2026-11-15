"""Alert decision layer.

POLICY ON TOP OF THE FROZEN MODEL. The ensemble, the scaler and the decision
threshold are untouched: `label` in the /predict response is still the model's
raw fall/adl call. This layer only decides when that call becomes an *alert*.

Why it exists: at the tuned threshold (95% recall) the model also flags a share
of resting windows (ADL recall ~71%), so the raw label alone cannot be used to
raise alerts. A real fall has a physical signature the model score alone does
not enforce:

    1. IMPACT      - one window contains a sharp acceleration peak, and the
                     model scores that window at or above the threshold.
    2. NO RECOVERY - the following windows are still (the person is not
                     getting up / walking away).
    3. NOT UPRIGHT - while still, the board is not in the upright posture
                     (gravity is not along the standing Y axis). A hard sit or
                     a stumble ends upright; a fall ends flat or on the side.

Only when all three happen is the fall "confirmed" and an alert raised. Sustained
shaking or walking never confirms, because activity resumes. Resting windows
never start a candidate, because they contain no impact, whatever the model
scores them.

ALL NUMBERS BELOW ARE DEFAULTS FROM ONE PERSON IN ONE SESSION (pad drops, not
real falls). Re-check them with tools/gate_analysis.py before relying on them.
A gate set too strict will miss soft falls (recall loss); too loose will not help.

The posture check assumes the board is worn in the standing orientation
(standing => mean ay about -257 counts). If the board is worn differently, set
POSTURE_CHECK = False or re-derive POSTURE_UPRIGHT_Y_COUNTS.

State is kept in memory per device (single uvicorn process). It is lost on a
restart, which only means an in-progress confirmation is forgotten.
"""
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

COUNTS_PER_G = 256.0          # incoming windows are SisFall-equivalent counts (256 counts/g)

# ---- Tunable policy parameters ----
IMPACT_PEAK_G = 1.8           # candidate window must contain a peak |a| >= this
STILL_PEAK_G = 1.35           # follow-up window is "still" if its peak |a| < this (rest ~ 1.0-1.1 g)
RECOVERY_PEAK_G = 1.8         # follow-up window with peak |a| >= this means the person is active again
CONFIRM_STILL_WINDOWS = 3     # still follow-up windows needed (total = 1 impact + 3 = 4 windows)
PENDING_TTL_S = 90            # forget a pending candidate if confirmation takes longer than this

# Posture check (new). Upright standing/sitting has |mean ay| ~ 245-260 counts;
# every captured fall ended with |mean ay| <= ~95 counts (flat or on its side).
POSTURE_CHECK = True
POSTURE_UPRIGHT_Y_COUNTS = 150.0   # average |mean ay| of the still windows at or above this = upright = not a fall


@dataclass
class Decision:
    action: str               # normal | pending_started | pending_restarted | confirming | holding | recovered | confirmed
    detail: str = ""
    # Only set on "confirmed": the impact window that started the candidate. This is the window the
    # caregiver's later verification (fall / false alarm) labels, so continual learning keeps it.
    window: Optional[np.ndarray] = None

    @property
    def text(self) -> str:
        return f"{self.action} {self.detail}".strip()


@dataclass
class _Pending:
    started_at: float
    still_seen: int
    impact_peak_g: float
    probability: float
    window: Optional[np.ndarray] = None
    still_y_sum: float = 0.0   # sum of |mean ay| over still windows that reported it
    still_y_n: int = 0


_lock = threading.Lock()
_pending: dict = {}


def _keep(window: Optional[np.ndarray]) -> Optional[np.ndarray]:
    return None if window is None else np.array(window, dtype=np.float32, copy=True)


def window_features(window: np.ndarray) -> dict:
    """window: (512, 6) in SisFall-equivalent counts [ax, ay, az, gx, gy, gz]."""
    w = np.asarray(window, dtype=float)
    acc_g = w[:, :3] / COUNTS_PER_G
    svm = np.sqrt((acc_g ** 2).sum(axis=1))
    return {
        "peak_svm_g": float(svm.max()),
        "mean_svm_g": float(svm.mean()),
        "std_svm_g": float(svm.std()),
        "mean_y_counts": float(w[:, 1].mean()),   # posture: about -257 when standing upright
    }


def reset_state() -> None:
    with _lock:
        _pending.clear()


def evaluate_window(device_id: str, probability: float, threshold: float,
                    features: dict, now: float = None, window: Optional[np.ndarray] = None) -> Decision:
    now = time.time() if now is None else now
    peak = features["peak_svm_g"]
    is_candidate = probability >= threshold and peak >= IMPACT_PEAK_G

    with _lock:
        pend = _pending.get(device_id)
        if pend is not None and now - pend.started_at > PENDING_TTL_S:
            del _pending[device_id]
            pend = None

        if pend is None:
            if is_candidate:
                _pending[device_id] = _Pending(now, 0, peak, probability, _keep(window))
                return Decision("pending_started", f"peak={peak:.2f}g p={probability:.2f}")
            return Decision("normal")

        # A candidate impact is already waiting for confirmation.
        if peak >= RECOVERY_PEAK_G:
            if is_candidate:
                _pending[device_id] = _Pending(now, 0, peak, probability, _keep(window))
                return Decision("pending_restarted", f"peak={peak:.2f}g")
            del _pending[device_id]
            return Decision("recovered", f"peak={peak:.2f}g")

        if peak < STILL_PEAK_G:
            pend.still_seen += 1
            y = features.get("mean_y_counts")
            if y is not None:
                pend.still_y_sum += abs(float(y))
                pend.still_y_n += 1
            if pend.still_seen >= CONFIRM_STILL_WINDOWS:
                del _pending[device_id]
                # Posture check. If no still window reported posture, do not block the alert.
                if POSTURE_CHECK and pend.still_y_n > 0:
                    avg_y = pend.still_y_sum / pend.still_y_n
                    if avg_y >= POSTURE_UPRIGHT_Y_COUNTS:
                        return Decision("recovered", f"upright |y|={avg_y:.0f}")
                return Decision("confirmed", f"impact={pend.impact_peak_g:.2f}g", window=pend.window)
            return Decision("confirming", f"{pend.still_seen}/{CONFIRM_STILL_WINDOWS}")

        # Between "still" and "active": neither advances nor cancels.
        return Decision("holding", f"peak={peak:.2f}g")