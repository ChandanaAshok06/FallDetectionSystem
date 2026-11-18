"""Send a test web push to a caregiver account, no fall needed.

Run from the backend folder, in the same shell setup as the server
(VAPID_PRIVATE_KEY_FILE and VAPID_SUBJECT set):

    python tools/send_test_push.py --email caregiver@example.com
    python tools/send_test_push.py --list              (shows accounts and how many browsers/phones are subscribed)

The caregiver must be logged in once with notifications turned on, so a subscription exists.
It reads the same fall_detection.db as the server, so run it from the backend folder.
"""
import argparse
import logging
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.core import webpush                                 # noqa: E402
from app.db.models import PushSubscription, User             # noqa: E402
from app.db.session import SessionLocal                      # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--email", help="the caregiver account's e-mail")
    ap.add_argument("--list", action="store_true", help="list accounts and their subscription counts")
    ap.add_argument("--title", default="Test notification")
    ap.add_argument("--body", default="Web push is working. This is a test, not a real fall alert.")
    args = ap.parse_args()

    if not Path("fall_detection.db").exists():
        print("fall_detection.db not found here. Run this from the backend folder.", file=sys.stderr)
        return 1
    if not args.email and not args.list:
        ap.print_usage(sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    db = SessionLocal()
    try:
        if args.list:
            for u in db.query(User).all():
                n = db.query(PushSubscription).filter(PushSubscription.user_id == u.id).count()
                print(f"{u.caregiver_email:<35} {n} subscription(s)")
            return 0

        user = db.query(User).filter(User.caregiver_email == args.email.strip()).first()
        if user is None:
            print(f"No account with e-mail {args.email!r}. Use --list to see the accounts.", file=sys.stderr)
            return 1
        subs = db.query(PushSubscription).filter(PushSubscription.user_id == user.id).count()
        user_id = user.id
    finally:
        db.close()

    if not webpush.is_configured():
        print("Web push is not configured in this shell. Set both:\n"
              '  $env:VAPID_PRIVATE_KEY_FILE="vapid_private.pem"   (the file must exist)\n'
              '  $env:VAPID_SUBJECT="mailto:you@your-domain.com"', file=sys.stderr)
        return 1
    if subs == 0:
        print("This account has no push subscriptions. Log in to the app as this caregiver and turn "
              "notifications on (allow the browser prompt), then run this again.", file=sys.stderr)
        return 1

    sent = webpush.send_to_user(user_id, args.title, args.body)
    print(f"{subs} subscription(s) found, {sent} accepted by the push service.")
    if sent < subs:
        print("Some were not accepted; see the 'fall.webpush' lines above. HTTP 403 usually means the "
              "browser subscribed with a different public key: reset the site's notification permission "
              "and turn notifications on again. Expired subscriptions are removed automatically.")
        return 1
    print("Check the browser or phone for the notification.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
