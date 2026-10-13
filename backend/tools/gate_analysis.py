"""Inspect SD-card captures and see what the alert decision layer would do.

Usage (from the backend folder, in your venv):
    python tools/gate_analysis.py D:\\path\\to\\sdcard\\capture_01150.csv capture_01151.csv ...
    python tools/gate_analysis.py D:\\path\\to\\sdcard            # every capture_*.csv in the folder

For each capture it prints the motion features the gate uses (peak |a| in g, mean
and std). It then replays the captures IN FILENAME ORDER as one stream through the
real decision layer, assuming the model flagged EVERY window as "fall"
(probability 1.0). That is the worst case, so:

  * If a resting/walking sequence still produces no CONFIRMED alert here, the gate
    alone is enough to keep that motion from alerting, whatever the model scores.
  * A real fall capture should show: a window with a high peak, then still windows,
    then "confirmed". If it does not, the gate is too strict for your device.

This does NOT run the model, so it cannot tell you the model's probability. It checks the gate
only. Thresholds in app/core/decision.py are unvalidated defaults: label a handful
of captures (rest, walking, shaking, controlled falls) and tune against them.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core import decision  # noqa: E402  (pure numpy, no TensorFlow needed)


def collect(args):
    files = []
    for a in args:
        p = Path(a)
        files += sorted(p.glob("capture_*.csv")) if p.is_dir() else [p]
    return sorted(files, key=lambda p: p.name)


def main(argv):
    files = collect(argv)
    if not files:
        print(__doc__)
        return 1

    decision.reset_state()
    print(f"{'file':<22}{'peak_g':>8}{'mean_g':>8}{'std_g':>8}   gate decision (model assumed = fall)")
    t = 0.0
    for f in files:
        try:
            w = np.loadtxt(f, delimiter=",", skiprows=1)
        except Exception as exc:
            print(f"{f.name:<22} could not read ({type(exc).__name__})")
            continue
        if w.ndim != 2 or w.shape[1] != 6:
            print(f"{f.name:<22} unexpected shape {w.shape}, skipped")
            continue
        feats = decision.window_features(w)
        t += 3.0  # windows are ~2.56 s plus save/POST time; only matters for the pending timeout
        d = decision.evaluate_window("analysis", 1.0, 0.2926, feats, now=t)
        print(f"{f.name:<22}{feats['peak_svm_g']:>8.2f}{feats['mean_svm_g']:>8.2f}"
              f"{feats['std_svm_g']:>8.2f}   {d.text}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
