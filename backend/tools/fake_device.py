r"""Fake ESP32 for testing device pairing on your computer (no hardware needed).

Usage (from the backend folder, with the server running):
    ..\.venv\Scripts\python.exe tools\fake_device.py
    ..\.venv\Scripts\python.exe tools\fake_device.py --code FD-4F2A91
    ..\.venv\Scripts\python.exe tools\fake_device.py --test-predict   (also sends one all-zero window)

It announces a pairing code, then polls until you enter that code in the app. The key is never
printed in full. --test-predict adds one 'adl' prediction row to the database.
"""
import argparse
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


def post(url, body, headers=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except urllib.error.URLError as e:
        print(f"Cannot reach the server: {e.reason}. Is it running?")
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--code", default="FD-" + secrets.token_hex(3).upper())
    ap.add_argument("--test-predict", action="store_true")
    args = ap.parse_args()

    status, body = post(f"{args.server}/devices/pair", {"code": args.code})
    if status != 200:
        print(f"Announce failed (HTTP {status}): {body}")
        sys.exit(1)
    token = body["poll_token"]
    interval = body.get("poll_interval_seconds", 3)
    deadline = time.time() + body.get("expires_in_seconds", 600)

    print(f"\nPairing code: {args.code}")
    print("Enter this code in the app: Devices > Pair a Device with its Code.")
    print(f"Waiting (the code expires in {body.get('expires_in_seconds', 600) // 60} minutes)...\n")

    while time.time() < deadline:
        status, body = post(
            f"{args.server}/devices/pair/credentials",
            {"code": args.code, "poll_token": token},
        )
        if status == 202:
            print(".", end="", flush=True)
            time.sleep(interval)
            continue
        if status == 200:
            key = body["device_key"]
            print("\n\nPAIRED.")
            print(f"  device_id : {body['device_id']}")
            print(f"  device_key: {key[:4]}...{key[-4:]} ({len(key)} characters, hidden)")
            print("  A real device would save both to flash (NVS) here.")
            if args.test_predict:
                send_test_window(args.server, body["device_id"], key)
            return
        print(f"\nStopped (HTTP {status}): {body}")
        sys.exit(1)

    print("\nTimed out: nobody claimed the code in time.")
    sys.exit(1)


def send_test_window(server, device_id, key):
    window = {
        "device_id": device_id,
        "window_start": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "sample_rate_hz": 200,
        "data": [[0.0] * 6 for _ in range(512)],
    }
    status, body = post(f"{server}/predict", window, headers={"X-Device-Key": key})
    print(f"\nTest /predict with the delivered key: HTTP {status}")
    if status == 200:
        print(f"  label={body.get('label')}  alert_id={body.get('alert_id')}  (all-zero window, so no alert is expected)")
    else:
        print(f"  {body}")


if __name__ == "__main__":
    main()