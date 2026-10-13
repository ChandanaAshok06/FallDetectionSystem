"""Trainer for the single personal model of one device. Runs as its own process:

    python -m app.continual.train --model-id <cl_models.id>      (cwd = backend folder)

What it does
------------
* Loads ALL verified samples for the device (caregiver-verified impact windows, label 1 = fall,
  0 = false alarm), scales them with the SAME StandardScaler as the global pipeline.
* Fine-tunes a fresh COPY of one global model (CL_BASE_MODEL) starting from the global weights, every
  time, on all samples so far (so retraining cannot drift away from what the data supports). By default only the
  Dense head is trained; the convolution/LSTM feature extractor stays frozen, because there are only
  a few dozen samples. (CL_UNFREEZE_LSTM=1 also trains the Bidirectional LSTM.)
* Small label-preserving augmentation (amplitude +-10 %, noise, +-25-sample shift) and balanced class weights.
* PROMOTION GUARD: the candidate (blended exactly as it would be served) is compared with the global
  ensemble alone on the same samples at the same 0.2926 threshold. It goes live only if it is NOT worse on
  fall recall or false-alarm rate, and strictly better on at least one. If held-out samples exist (>= 4 of
  each class) it must also be not worse on those. Otherwise it is rejected and the previous version keeps
  serving.

HONEST LIMITS (also reported in the stored metrics): with a few dozen samples, and the first model
usually having NO held-out validation, the guard is a sanity check, not proof of improvement. Alert
feedback can only label windows that raised an alert, so missed falls are never in the data.
"""
import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np

from app.continual import store
from app.db import session as dbsession

log = logging.getLogger("fall.continual")


# ------------------------------------------------------------------ helpers
def _rates(p, y, thr):
    p, y = np.asarray(p), np.asarray(y)
    pred = p >= thr
    P, N = int((y == 1).sum()), int((y == 0).sum())
    return {
        "recall": float((pred & (y == 1)).sum() / P) if P else None,
        "fpr": float((pred & (y == 0)).sum() / N) if N else None,
        "n_pos": P, "n_neg": N,
    }


def _compare(new, old):
    """Return (worse, better) metric-name lists for new vs old on the same samples."""
    worse, better = [], []
    if new["recall"] is not None:
        if new["recall"] < old["recall"] - 1e-9:
            worse.append("fall_recall")
        elif new["recall"] > old["recall"] + 1e-9:
            better.append("fall_recall")
    if new["fpr"] is not None:
        if new["fpr"] > old["fpr"] + 1e-9:
            worse.append("false_alarm_rate")
        elif new["fpr"] < old["fpr"] - 1e-9:
            better.append("false_alarm_rate")
    return worse, better


def _split(y, rng):
    pos, neg = np.where(y == 1)[0], np.where(y == 0)[0]
    if len(pos) < 4 or len(neg) < 4:                      # too few to hold anything out
        return np.arange(len(y)), np.array([], dtype=int)
    val = []
    for idx in (pos, neg):
        idx = rng.permutation(idx)
        val += list(idx[:max(1, int(round(len(idx) * 0.25)))])
    val = np.array(val)
    return np.setdiff1d(np.arange(len(y)), val), val


def _augment(X, y, k, rng):
    outs, ys = [X], [y]
    for _ in range(max(k - 1, 0)):
        amp = rng.uniform(0.9, 1.1, (len(X), 1, 1)).astype(np.float32)
        noise = rng.normal(0, 0.05, X.shape).astype(np.float32)
        shifts = rng.integers(-25, 26, len(X))
        shifted = np.stack([np.roll(x, int(s), axis=0) for x, s in zip(X, shifts)])
        outs.append(shifted * amp + noise)
        ys.append(y)
    return np.concatenate(outs), np.concatenate(ys)


def _freeze(model, unfreeze_lstm):
    for layer in model.layers:
        kind = layer.__class__.__name__
        layer.trainable = (kind == "Dense") or (unfreeze_lstm and kind == "Bidirectional")


def _load_samples(db, device_id):
    X, y = [], []
    for s in db.query(store.ContinualSample).filter(store.ContinualSample.device_id == device_id).all():
        p = Path(s.window_path)
        if not p.exists():
            continue
        arr = np.load(p)
        if arr.shape == (512, 6):
            X.append(arr.astype(np.float32))
            y.append(int(s.label))
    if not X:
        raise ValueError("no usable samples")
    return np.stack(X), np.array(y)


# ------------------------------------------------------------------ main routine
def _train(db, row):
    import tensorflow as tf
    from tensorflow.keras.models import load_model
    from tensorflow.keras.optimizers import Adam
    from app.inference.ensemble import run_ensemble, THRESHOLD

    cfg = store.cfg
    X, y = _load_samples(db, row.device_id)
    if len(set(y.tolist())) < 2:
        raise ValueError("need at least one verified fall and one false alarm")

    scaler = joblib.load(store.MODELS_DIR / "standard_scaler.pkl")
    Xs = scaler.transform(X.reshape(-1, 6)).reshape(-1, 512, 6).astype(np.float32)

    rng = np.random.default_rng(row.version)
    tf.keras.utils.set_random_seed(int(row.version))
    tr, va = _split(y, rng)
    Xa, ya = _augment(Xs[tr], y[tr], cfg.augment_factor, rng)

    model = load_model(store.MODELS_DIR / cfg.base_model)          # fresh copy of the global model
    _freeze(model, cfg.unfreeze_lstm)
    model.compile(optimizer=Adam(cfg.lr), loss="binary_crossentropy")
    n0, n1 = int((ya == 0).sum()), int((ya == 1).sum())
    class_weight = {0: len(ya) / (2.0 * n0), 1: len(ya) / (2.0 * n1)}
    # With only a few dozen samples one epoch is just a handful of gradient steps, so a fixed epoch
    # count can leave the model unchanged. Guarantee a minimum number of steps instead.
    steps_per_epoch = -(-len(ya) // 8)
    epochs = max(cfg.epochs, -(-cfg.min_steps // steps_per_epoch))
    hist = model.fit(Xa, ya, epochs=epochs, batch_size=8, class_weight=class_weight,
                     shuffle=True, verbose=0)

    # ---- promotion guard: candidate (as it will be served) vs the global ensemble alone
    w = 1.0 if cfg.mode == "replace" else store.blend_weight(len(y))
    p_global = np.array([run_ensemble(x) for x in X])
    p_personal = model.predict(Xs, verbose=0)[:, 0]
    p_cand = (1.0 - w) * p_global + w * p_personal

    all_new, all_old = _rates(p_cand, y, THRESHOLD), _rates(p_global, y, THRESHOLD)
    worse_all, better_all = _compare(all_new, all_old)
    reasons, promote = [], True
    if worse_all:
        promote = False
        reasons.append("worse than the global ensemble on: " + ", ".join(worse_all))
    if not better_all:
        promote = False
        reasons.append("no improvement over the global ensemble on the verified samples")

    val_info = {"available": bool(len(va))}
    if len(va):
        v_new, v_old = _rates(p_cand[va], y[va], THRESHOLD), _rates(p_global[va], y[va], THRESHOLD)
        worse_val, _ = _compare(v_new, v_old)
        val_info.update({"candidate": v_new, "global": v_old})
        if worse_val:
            promote = False
            reasons.append("worse than the global ensemble on held-out samples: " + ", ".join(worse_val))

    path = None
    if promote:
        path = store.PERSONAL_MODELS_DIR / row.device_id / f"v{row.version}.keras"
        path.parent.mkdir(parents=True, exist_ok=True)
        model.save(path)

    return {
        "promoted": promote,
        "reason": "passed the guard" if promote else "; ".join(reasons),
        "samples": {"total": int(len(y)), "falls": int((y == 1).sum()), "false_alarms": int((y == 0).sum()),
                    "train": int(len(tr)), "held_out": int(len(va))},
        "all_samples": {"candidate": all_new, "global": all_old,
                        "note": "includes samples the model trained on, so optimistic"},
        "held_out": val_info,
        "threshold": THRESHOLD,
        "config": {"base_model": cfg.base_model, "mode": cfg.mode, "blend_w": cfg.blend_w,
                   "epochs_requested": cfg.epochs, "epochs_run": epochs, "min_steps": cfg.min_steps, "augment_factor": cfg.augment_factor, "lr": cfg.lr,
                   "unfreeze_lstm": cfg.unfreeze_lstm},
        "final_train_loss": float(hist.history["loss"][-1]),
    }, (str(path) if path else None)


def train_for_row(row_id: str) -> dict:
    db = dbsession.SessionLocal()
    try:
        row = db.query(store.ContinualModel).filter(store.ContinualModel.id == row_id).first()
        if row is None:
            return {"error": "unknown training row"}
        try:
            metrics, path = _train(db, row)
            row.status = "active" if metrics["promoted"] else "rejected"
            row.model_path = path
            row.metrics = json.dumps(metrics)
            if metrics["promoted"]:        # older live versions are superseded
                for old in (db.query(store.ContinualModel)
                            .filter(store.ContinualModel.device_id == row.device_id,
                                    store.ContinualModel.status == "active",
                                    store.ContinualModel.id != row.id).all()):
                    old.status = "superseded"
        except Exception as exc:
            row.status = "failed"
            row.metrics = json.dumps({"error": f"{type(exc).__name__}: {str(exc)[:300]}"})
            log.error("Personal-model training failed: %s", type(exc).__name__)
        row.finished_at = datetime.utcnow()
        db.commit()
        store.invalidate_cache(row.device_id)
        return json.loads(row.metrics)
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-id", required=True)
    args = ap.parse_args()
    out = train_for_row(args.model_id)
    print(json.dumps(out, indent=2))
    sys.exit(1 if "error" in out else 0)
