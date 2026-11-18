"""Web push: sender, delivery wiring, escalation, key generator, and a real encrypted push.

No network is used. `_send` is replaced in most tests; the last tests let pywebpush encrypt and
sign for real and only intercept the final HTTP request.
"""
import base64
import json
import os
import subprocess
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.core import escalation, notify, webpush
from app.db.models import Alert, Device, PushSubscription

BACKEND = Path(__file__).resolve().parent.parent


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@pytest.fixture()
def push_env(monkeypatch, session_factory):
    """Push configured, sender pointed at the test database."""
    monkeypatch.setattr(webpush, "SessionLocal", session_factory)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "not-a-real-key")     # _send is replaced in these tests
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:test@example.com")
    monkeypatch.delenv("VAPID_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.delenv("WEBPUSH_TTL_S", raising=False)


def user_id_of(session_factory, user):
    with session_factory() as db:
        return db.query(Device).filter(Device.id == user["device_id"]).one().user_id


def add_sub(session_factory, user_id, name, p256dh="p", auth="a"):
    with session_factory() as db:
        db.add(PushSubscription(user_id=user_id, endpoint=f"https://push.example/{name}",
                                p256dh=p256dh, auth=auth))
        db.commit()
    return f"https://push.example/{name}"


def endpoints(session_factory):
    with session_factory() as db:
        return sorted(s.endpoint for s in db.query(PushSubscription).all())


class Recorder:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail or {}

    def __call__(self, info, payload, key, subject, ttl):
        self.calls.append({"endpoint": info["endpoint"], "payload": json.loads(payload),
                           "key": key, "subject": subject, "ttl": ttl})
        err = self.fail.get(info["endpoint"])
        if err:
            raise err


# ---------------------------------------------------------------- configuration
def test_not_configured_sends_nothing(monkeypatch, session_factory, user_a):
    monkeypatch.setattr(webpush, "SessionLocal", session_factory)
    for var in ("VAPID_PRIVATE_KEY", "VAPID_PRIVATE_KEY_FILE", "VAPID_SUBJECT"):
        monkeypatch.delenv(var, raising=False)
    add_sub(session_factory, user_id_of(session_factory, user_a), "a1")
    monkeypatch.setattr(webpush, "_send", lambda *a: pytest.fail("must not send when unconfigured"))
    assert webpush.is_configured() is False
    assert webpush.send_to_user(user_id_of(session_factory, user_a), "t", "b") == 0


def test_subject_must_be_mailto_or_https(monkeypatch, push_env):
    monkeypatch.setenv("VAPID_SUBJECT", "admin@example.com")
    assert webpush.is_configured() is False
    monkeypatch.setenv("VAPID_SUBJECT", "https://example.com")
    assert webpush.is_configured() is True


def test_missing_key_file_means_not_configured(monkeypatch, push_env, tmp_path):
    monkeypatch.setenv("VAPID_PRIVATE_KEY_FILE", str(tmp_path / "nope.pem"))
    assert webpush.is_configured() is False


# ---------------------------------------------------------------- sender
def test_sends_only_to_the_owner_with_expected_payload(monkeypatch, push_env, session_factory, user_a, user_b):
    ua, ub = user_id_of(session_factory, user_a), user_id_of(session_factory, user_b)
    a1, a2 = add_sub(session_factory, ua, "a1"), add_sub(session_factory, ua, "a2")
    add_sub(session_factory, ub, "b1")
    rec = Recorder()
    monkeypatch.setattr(webpush, "_send", rec)

    sent = webpush.send_to_user(ua, "Possible fall detected", "POSSIBLE FALL\ncheck now")

    assert sent == 2
    assert sorted(c["endpoint"] for c in rec.calls) == [a1, a2]
    c = rec.calls[0]
    assert c["payload"] == {"title": "Possible fall detected", "body": "POSSIBLE FALL check now", "url": "/alerts"}
    assert c["subject"] == "mailto:test@example.com" and c["ttl"] == 600


def test_ttl_is_configurable(monkeypatch, push_env, session_factory, user_a):
    ua = user_id_of(session_factory, user_a)
    add_sub(session_factory, ua, "a1")
    monkeypatch.setenv("WEBPUSH_TTL_S", "120")
    rec = Recorder()
    monkeypatch.setattr(webpush, "_send", rec)
    webpush.send_to_user(ua, "t", "b")
    assert rec.calls[0]["ttl"] == 120


def test_long_body_is_trimmed(monkeypatch, push_env, session_factory, user_a):
    ua = user_id_of(session_factory, user_a)
    add_sub(session_factory, ua, "a1")
    rec = Recorder()
    monkeypatch.setattr(webpush, "_send", rec)
    webpush.send_to_user(ua, "t", "x" * 1000)
    assert len(rec.calls[0]["payload"]["body"]) == webpush.MAX_BODY_CHARS


def test_expired_subscription_is_deleted_others_kept(monkeypatch, push_env, session_factory, user_a):
    ua = user_id_of(session_factory, user_a)
    gone, ok = add_sub(session_factory, ua, "gone"), add_sub(session_factory, ua, "ok")
    monkeypatch.setattr(webpush, "_send", Recorder(fail={gone: webpush.SubscriptionExpired()}))
    assert webpush.send_to_user(ua, "t", "b") == 1
    assert endpoints(session_factory) == [ok]


def test_other_failures_keep_the_subscription_and_do_not_stop_the_rest(monkeypatch, push_env, session_factory, user_a):
    ua = user_id_of(session_factory, user_a)
    first, second = add_sub(session_factory, ua, "a1"), add_sub(session_factory, ua, "a2")
    rec = Recorder(fail={first: RuntimeError("push service answered HTTP 503"), second: None})
    monkeypatch.setattr(webpush, "_send", rec)
    assert webpush.send_to_user(ua, "t", "b") == 1             # one failed, one accepted
    assert endpoints(session_factory) == sorted([first, second])


def test_user_without_subscriptions_sends_nothing(monkeypatch, push_env, session_factory, user_a):
    monkeypatch.setattr(webpush, "_send", lambda *a: pytest.fail("no subscriptions"))
    assert webpush.send_to_user(user_id_of(session_factory, user_a), "t", "b") == 0


# ---------------------------------------------------------------- delivery wiring
def _quiet_channels(monkeypatch):
    monkeypatch.setattr(notify, "_send_telegram", lambda text: False)
    monkeypatch.setattr(notify, "_send_email", lambda to, subject, text: False)


def test_deliver_external_pushes_to_the_given_user(monkeypatch):
    _quiet_channels(monkeypatch)
    calls = []
    monkeypatch.setattr(webpush, "send_to_user", lambda *a, **k: calls.append(a) or 1)
    notify.deliver_external("fall text", subject="Possible fall detected", user_id="user-1").join(5)
    assert calls == [("user-1", "Possible fall detected", "fall text")]


def test_deliver_external_without_user_does_not_push(monkeypatch):
    _quiet_channels(monkeypatch)
    monkeypatch.setattr(webpush, "send_to_user", lambda *a, **k: pytest.fail("no user, no push"))
    notify.deliver_external("fall text", subject="s").join(5)


def test_a_telegram_failure_does_not_block_push_or_email(monkeypatch):
    calls = []

    def boom(text):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(notify, "_send_telegram", boom)
    monkeypatch.setattr(notify, "_send_email", lambda to, s, t: calls.append("email") or True)
    monkeypatch.setattr(webpush, "send_to_user", lambda *a, **k: calls.append("push") or 1)
    notify.deliver_external("t", subject="s", email_to="c@example.com", user_id="u").join(5)
    assert calls == ["push", "email"]


def test_escalation_pushes_to_the_devices_caregiver(monkeypatch, session_factory, user_a):
    ua = user_id_of(session_factory, user_a)
    with session_factory() as db:
        db.add(Alert(prediction_id="p1", device_id=user_a["device_id"], status="active",
                     created_at=datetime.utcnow() - timedelta(seconds=600)))
        db.commit()
    monkeypatch.setattr(escalation, "SessionLocal", session_factory)
    seen = []
    monkeypatch.setattr(notify, "deliver_external", lambda *a, **k: seen.append(k))

    assert len(escalation.escalate_overdue()) == 1
    assert seen and seen[0]["user_id"] == ua and seen[0]["subject"] == "NO RESPONSE - possible fall"


# ---------------------------------------------------------------- real signing and encryption
def _real_subscription():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    browser_key = ec.generate_private_key(ec.SECP256R1())
    pub = browser_key.public_key().public_bytes(serialization.Encoding.X962,
                                                serialization.PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    return browser_key, auth, {"endpoint": "https://push.example/real", "keys": {"p256dh": b64url(pub), "auth": b64url(auth)}}


def _generate_key_with_script(tmp_path, subject="mailto:test@example.com"):
    out = tmp_path / "vapid_private.pem"
    r = subprocess.run(
        [sys.executable, str(BACKEND / "tools" / "generate_vapid_keys.py"), "--subject", subject, "--out", str(out)],
        capture_output=True, text=True, cwd=BACKEND)
    return out, r


def test_key_generator_writes_a_usable_key_and_refuses_to_overwrite(tmp_path):
    out, r = _generate_key_with_script(tmp_path)
    assert r.returncode == 0, r.stderr
    assert out.is_file()
    public = [l.split("=", 1)[1] for l in r.stdout.splitlines() if l.strip().startswith("VITE_VAPID_PUBLIC_KEY=")][0]
    raw = base64.urlsafe_b64decode(public + "=" * (-len(public) % 4))
    assert len(raw) == 65 and raw[0] == 4                       # uncompressed P-256 point
    assert "VAPID_PRIVATE_KEY_FILE=vapid_private.pem" in r.stdout

    again = subprocess.run(
        [sys.executable, str(BACKEND / "tools" / "generate_vapid_keys.py"), "--subject", "mailto:x@y.z", "--out", str(out)],
        capture_output=True, text=True, cwd=BACKEND)
    assert again.returncode == 1 and "already exists" in again.stderr

    bad = subprocess.run([sys.executable, str(BACKEND / "tools" / "generate_vapid_keys.py"), "--subject", "nope"],
                         capture_output=True, text=True, cwd=BACKEND)
    assert bad.returncode == 2


def test_real_push_is_signed_with_the_generated_key_and_decrypts(monkeypatch, push_env, session_factory, user_a, tmp_path):
    import http_ece
    import requests

    out, r = _generate_key_with_script(tmp_path)
    assert r.returncode == 0, r.stderr
    monkeypatch.setenv("VAPID_PRIVATE_KEY_FILE", str(out))

    browser_key, auth, info = _real_subscription()
    ua = user_id_of(session_factory, user_a)
    with session_factory() as db:
        db.add(PushSubscription(user_id=ua, endpoint=info["endpoint"],
                                p256dh=info["keys"]["p256dh"], auth=info["keys"]["auth"]))
        db.commit()

    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None, **kw):
        captured.update(url=url, data=data, headers=dict(headers), timeout=timeout)
        return types.SimpleNamespace(status_code=201, reason="Created", text="", ok=True, headers={})

    monkeypatch.setattr(requests, "post", fake_post)

    assert webpush.send_to_user(ua, "Possible fall detected", "Check on Mom now.") == 1

    h = {k.lower(): v for k, v in captured["headers"].items()}
    assert captured["url"] == info["endpoint"]
    assert h["authorization"].lower().startswith("vapid ")      # signed with our VAPID key
    assert h["ttl"] == "600" and h["urgency"] == "high"
    assert b"Possible fall" not in captured["data"]             # encrypted on the wire

    plain = http_ece.decrypt(captured["data"], private_key=browser_key, auth_secret=auth, version="aes128gcm")
    assert json.loads(plain) == {"title": "Possible fall detected", "body": "Check on Mom now.", "url": "/alerts"}


def test_real_push_410_from_the_push_service_removes_the_subscription(monkeypatch, push_env, session_factory, user_a, tmp_path):
    import requests

    out, r = _generate_key_with_script(tmp_path)
    monkeypatch.setenv("VAPID_PRIVATE_KEY_FILE", str(out))
    browser_key, auth, info = _real_subscription()
    ua = user_id_of(session_factory, user_a)
    with session_factory() as db:
        db.add(PushSubscription(user_id=ua, endpoint=info["endpoint"],
                                p256dh=info["keys"]["p256dh"], auth=info["keys"]["auth"]))
        db.commit()

    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: types.SimpleNamespace(status_code=410, reason="Gone", text="gone", ok=False, headers={}))
    assert webpush.send_to_user(ua, "t", "b") == 0
    assert endpoints(session_factory) == []


# ---------------------------------------------------------------- tools/send_test_push.py
@pytest.fixture()
def test_push_tool(monkeypatch, push_env, session_factory, tmp_path):
    """The script, loaded as a module, pointed at the test DB and run from a scratch folder."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("send_test_push", BACKEND / "tools" / "send_test_push.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "SessionLocal", session_factory)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "fall_detection.db").write_bytes(b"")           # the script only checks it exists

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["send_test_push.py", *argv])
        return mod.main()

    return run


def _email_of(session_factory, user):
    from app.db.models import User
    with session_factory() as db:
        return db.query(User).filter(User.id == user_id_of(session_factory, user)).one().caregiver_email


def test_tool_sends_a_test_push(monkeypatch, test_push_tool, session_factory, user_a, capsys):
    add_sub(session_factory, user_id_of(session_factory, user_a), "a1")
    rec = Recorder()
    monkeypatch.setattr(webpush, "_send", rec)
    assert test_push_tool("--email", _email_of(session_factory, user_a)) == 0
    assert rec.calls[0]["payload"]["title"] == "Test notification"
    assert "1 subscription(s) found, 1 accepted" in capsys.readouterr().out


def test_tool_explains_missing_subscription_unknown_email_and_missing_config(
        monkeypatch, test_push_tool, session_factory, user_a, capsys):
    email = _email_of(session_factory, user_a)
    monkeypatch.setattr(webpush, "_send", lambda *a: pytest.fail("nothing should be sent"))

    assert test_push_tool("--email", email) == 1                  # account exists, nobody subscribed
    assert "no push subscriptions" in capsys.readouterr().err

    assert test_push_tool("--email", "nobody@example.com") == 1
    assert "No account" in capsys.readouterr().err

    add_sub(session_factory, user_id_of(session_factory, user_a), "a1")
    monkeypatch.delenv("VAPID_PRIVATE_KEY")
    assert test_push_tool("--email", email) == 1
    assert "not configured" in capsys.readouterr().err


def test_tool_list_shows_subscription_counts(test_push_tool, session_factory, user_a, capsys):
    add_sub(session_factory, user_id_of(session_factory, user_a), "a1")
    assert test_push_tool("--list") == 0
    assert "1 subscription(s)" in capsys.readouterr().out


def test_tool_reports_partial_failure(monkeypatch, test_push_tool, session_factory, user_a, capsys):
    ua = user_id_of(session_factory, user_a)
    bad = add_sub(session_factory, ua, "bad")
    add_sub(session_factory, ua, "good")
    monkeypatch.setattr(webpush, "_send", Recorder(fail={bad: RuntimeError("push service answered HTTP 403")}))
    assert test_push_tool("--email", _email_of(session_factory, user_a)) == 1
    out = capsys.readouterr().out
    assert "2 subscription(s) found, 1 accepted" in out and "403" in out
