"""Generate the VAPID key pair used for web push.

Run once from the backend folder:

    python tools/generate_vapid_keys.py --subject mailto:you@example.com

It writes the private key to vapid_private.pem (git-ignored; keep it secret) and prints the
lines to put in the backend environment and in frontend/.env.

Rotating the keys invalidates every existing browser subscription: caregivers must turn
notifications on again. That is why the script refuses to overwrite an existing key unless
you pass --force.
"""
import argparse
import base64
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subject", required=True,
                    help='who is sending, e.g. "mailto:you@your-domain.com" (use a real address)')
    ap.add_argument("--out", default="vapid_private.pem", help="where to write the private key")
    ap.add_argument("--force", action="store_true", help="overwrite an existing key file")
    args = ap.parse_args()

    if not args.subject.startswith(("mailto:", "https://")):
        print('--subject must start with "mailto:" or "https://"', file=sys.stderr)
        return 2

    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"{out} already exists. Not overwriting (this would break existing subscriptions).\n"
              "Use --force only if you really want new keys.", file=sys.stderr)
        return 1

    vapid = Vapid()
    vapid.generate_keys()
    vapid.save_key(str(out))

    public_raw = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    public_b64 = base64.urlsafe_b64encode(public_raw).rstrip(b"=").decode()

    print(f"Private key written to {out}  (do NOT commit it)\n")
    print("Backend environment (e.g. in backend/.env or the shell that starts uvicorn):")
    print(f"  VAPID_PRIVATE_KEY_FILE={out.name}")
    print(f"  VAPID_SUBJECT={args.subject}\n")
    print("frontend/.env:")
    print(f"  VITE_VAPID_PUBLIC_KEY={public_b64}\n")
    print("Then restart the backend and rebuild/restart the frontend, and turn notifications on again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
