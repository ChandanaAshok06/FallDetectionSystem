"""Compare your ESP32 capture with the data the model was trained on.

Run from D:\\FallDetectionSystem\\backend (venv active):

  python check_sensor_match.py capture_00570.csv
        -> compares each channel with the training scaler (models_store\\standard_scaler.pkl)

  python check_sensor_match.py capture_00570.csv --sisfall D:\\path\\to\\SisFall_dataset\\SA01
        -> also finds the quietest 512-sample window in the SisFall files of that folder
           (or in one .txt file) and compares it with your capture, channel by channel.
SisFall .txt files: 9 comma-separated columns at 200 Hz; the first 6 (ADXL345 xyz, ITG3200 xyz) are used.
"""
import argparse, glob, os, sys
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("capture")
ap.add_argument("--sisfall", help="a SisFall .txt file or a folder of them (e.g. one subject folder SA01)")
ap.add_argument("--scaler", default=os.path.join("models_store", "standard_scaler.pkl"))
a = ap.parse_args()

names = ["acc_x", "acc_y", "acc_z", "gyro_x", "gyro_y", "gyro_z"]
cap = np.genfromtxt(a.capture, delimiter=",", skip_header=1)[:, :6]
print(f"capture: {cap.shape[0]} rows\n")

def table(title, cols):
    print(title)
    print(f"{'channel':8s}" + "".join(f"{c:>14s}" for c in cols[0]))
    for n, row in zip(names, cols[1]):
        print(f"{n:8s}" + "".join(f"{v:14.2f}" for v in row))
    print()

# 1) your capture's own mean / std
cm, cs = cap.mean(0), cap.std(0)

# 2) versus the training scaler
if os.path.exists(a.scaler):
    import joblib
    sc = joblib.load(a.scaler)
    z = (cm - sc.mean_) / sc.scale_
    print("vs TRAINING SCALER (mean/std of all SisFall training data)")
    print(f"{'channel':8s}{'capture mean':>14s}{'train mean':>14s}{'train std':>14s}{'z-score':>10s}")
    for i, n in enumerate(names):
        flag = "  <-- far off" if abs(z[i]) > 3 else ""
        print(f"{n:8s}{cm[i]:14.2f}{sc.mean_[i]:14.2f}{sc.scale_[i]:14.2f}{z[i]:10.2f}{flag}")
    print("(a resting window is not expected to be z=0, but |z| above ~3 on a channel means an offset or scale problem)\n")
else:
    print(f"scaler not found at {a.scaler} - run from the backend folder or pass --scaler\n")

# 3) versus the quietest SisFall window
if a.sisfall:
    files = [a.sisfall] if os.path.isfile(a.sisfall) else glob.glob(os.path.join(a.sisfall, "**", "*.txt"), recursive=True)
    best = None
    for f in files:
        try:
            txt = open(f).read().replace(";", "")
            d = np.array([[float(v) for v in l.split(",")[:6]] for l in txt.splitlines() if l.strip()])
        except Exception:
            continue
        for s in range(0, len(d) - 512, 64):
            w = d[s:s + 512]
            svm = np.linalg.norm(w[:, :3], axis=1)
            sc_ = svm.std()
            if best is None or sc_ < best[0]:
                best = (sc_, f, s, w)
    if best is None:
        print("no usable SisFall windows found")
    else:
        _, f, s, w = best
        print(f"quietest SisFall window: {os.path.basename(f)} samples {s}-{s+512}")
        print(f"{'channel':8s}{'capture mean':>14s}{'sisfall mean':>14s}{'capture std':>14s}{'sisfall std':>14s}")
        for i, n in enumerate(names):
            print(f"{n:8s}{cm[i]:14.2f}{w[:, i].mean():14.2f}{cs[i]:14.2f}{w[:, i].std():14.2f}")
