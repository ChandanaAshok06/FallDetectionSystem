"""
validate_scaler_fit.py

Purpose
-------
Confirms that calibrated MPU6500 sensor output, once converted into
SisFall-equivalent raw counts, lands in the same numerical space the
trained StandardScaler expects. This must pass BEFORE writing the
200 Hz / 512-sample acquisition firmware.

How it works
------------
1. Loads standard_scaler.pkl (the 6-channel scaler: acc_x, acc_y, acc_z,
   gyro_x, gyro_y, gyro_z) via joblib.
2. Takes calibrated MPU6500 samples as input, in PHYSICAL units:
     - accel: g  (e.g. 1.0 = 1g of acceleration)
     - gyro:  deg/s
   (This matches standard accel-calibration / gyro-bias-correction output.
   If your calibration code outputs something else -- e.g. raw LSB, or
   m/s^2 -- tell me and I'll adjust the conversion step.)
3. Converts physical units into SisFall-equivalent raw integer counts
   using the confirmed constants:
     SISFALL_ACCEL_LSB_PER_G = 256.0
     SISFALL_GYRO_LSB_PER_DPS = 14.375
4. Computes the z-score against the trained scaler: (x - mean_) / scale_
5. Flags any channel where |z| is unreasonably large (default: > 5),
   which would mean the converted value falls way outside the range the
   model was trained on -- a red flag before building firmware around it.

Usage
-----
Edit the `samples` list below with a few real calibrated MPU6500 readings
(stationary flat-on-table, then tilted, then a shake) and run:

    python3 validate_scaler_fit.py

Or import `convert_and_check()` and call it from your own test harness.
"""

import joblib
import numpy as np

# ---- Constants confirmed from SisFall investigation ----
SISFALL_ACCEL_LSB_PER_G = 256.0
SISFALL_GYRO_LSB_PER_DPS = 14.375

SCALER_PATH = "standard_scaler.pkl"  # place alongside this script
Z_SCORE_WARN_THRESHOLD = 5.0         # |z| beyond this = suspicious


def load_scaler(path=SCALER_PATH):
    scaler = joblib.load(path)
    assert scaler.n_features_in_ == 6, (
        f"Expected a 6-channel scaler, got {scaler.n_features_in_} features. "
        "Make sure you're pointing at standard_scaler.pkl, not feature_scaler.pkl."
    )
    return scaler


def convert_to_sisfall_counts(acc_g, gyro_dps):
    """
    acc_g:  (ax, ay, az) in g
    gyro_dps: (gx, gy, gz) in deg/s
    Returns a 6-element array in SisFall-equivalent raw counts,
    channel order: acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z
    """
    ax, ay, az = acc_g
    gx, gy, gz = gyro_dps
    acc_counts = np.array([ax, ay, az]) * SISFALL_ACCEL_LSB_PER_G
    gyro_counts = np.array([gx, gy, gz]) * SISFALL_GYRO_LSB_PER_DPS
    return np.concatenate([acc_counts, gyro_counts])


def convert_and_check(acc_g, gyro_dps, scaler, verbose=True):
    channels = ["acc_x", "acc_y", "acc_z", "gyro_x", "gyro_y", "gyro_z"]
    counts = convert_to_sisfall_counts(acc_g, gyro_dps)
    z_scores = (counts - scaler.mean_) / scaler.scale_

    flags = []
    if verbose:
        print(f"  {'channel':7s}  {'counts':>10s}  {'z-score':>10s}")
    for ch, c, z in zip(channels, counts, z_scores):
        flag = "  <-- CHECK THIS" if abs(z) > Z_SCORE_WARN_THRESHOLD else ""
        if flag:
            flags.append(ch)
        if verbose:
            print(f"  {ch:7s}  {c:10.2f}  {z:10.3f}{flag}")

    return counts, z_scores, flags


if __name__ == "__main__":
    scaler = load_scaler()

    # ---- EDIT THESE with real calibrated MPU6500 readings ----
    # Each entry: (label, acc_g (ax,ay,az), gyro_dps (gx,gy,gz))
    samples = [
    ("real capture 1",
     (-0.014, 0.027, 1.002),
     (0.05, -0.43, -0.17)),

    ("real capture 2",
     (-0.011, 0.024, 1.001),
     (0.15, -0.20, 0.08)),

    ("real capture 3",
     (-0.008, -0.041, 1.001),
     (-0.15, -0.03, 0.27)),
    ]

    all_flags = {}
    for label, acc_g, gyro_dps in samples:
        print(f"\n[{label}]")
        _, _, flags = convert_and_check(acc_g, gyro_dps, scaler)
        if flags:
            all_flags[label] = flags

    print("\n" + "=" * 50)
    if all_flags:
        print("RESULT: Some channels fell outside the expected range:")
        for label, flags in all_flags.items():
            print(f"  - {label}: {', '.join(flags)}")
        print(
            "\nThis doesn't automatically mean something is wrong -- large "
            "accel/gyro spikes during a real fall SHOULD produce big z-scores. "
            "But check that STILL / calm readings stay within a few std devs. "
            "If even still readings are far outside range, the conversion "
            "constants, axis convention, or calibration units likely don't "
            "match what SisFall used."
        )
    else:
        print("RESULT: All test samples landed within expected range.")