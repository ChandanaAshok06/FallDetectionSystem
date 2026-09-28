import csv
import getpass
import tkinter as tk
from tkinter import filedialog
from datetime import datetime, timezone

import requests


API_URL = "http://127.0.0.1:8000/predict"


def is_number(value):
    try:
        float(value)
        return True
    except ValueError:
        return False


# Get device credentials
device_id = input("Enter NEW device ID: ").strip()
device_key = getpass.getpass("Enter NEW device key: ").strip()


# Select CSV
root = tk.Tk()
root.withdraw()

csv_path = filedialog.askopenfilename(
    title="Select ESP32 CSV capture",
    filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
)

if not csv_path:
    print("No CSV selected.")
    raise SystemExit(1)


# Read CSV
with open(csv_path, "r", newline="") as f:
    reader = csv.reader(f)
    rows = list(reader)

print(f"\nSelected: {csv_path}")
print(f"CSV rows including header: {len(rows)}")


# Remove header if present
if rows and not all(is_number(x) for x in rows[0]):
    rows = rows[1:]


# Validate
if len(rows) != 512:
    raise ValueError(
        f"Expected exactly 512 sensor rows, but found {len(rows)}"
    )

data = []

for i, row in enumerate(rows):
    if len(row) != 6:
        raise ValueError(
            f"Row {i + 1} has {len(row)} columns; expected 6."
        )

    data.append([float(x) for x in row])


print("Validated sensor data: 512 rows × 6 channels")


# Build request
payload = {
    "device_id": device_id,
    "window_start": datetime.now(timezone.utc).isoformat(),
    "sample_rate_hz": 200,
    "data": data,
}


# Send prediction
response = requests.post(
    API_URL,
    json=payload,
    headers={
        "X-Device-Key": device_key
    },
    timeout=120,
)


print("\nHTTP status:", response.status_code)
print("Response:")

try:
    print(response.json())
except Exception:
    print(response.text)