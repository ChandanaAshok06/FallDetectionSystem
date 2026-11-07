from datetime import datetime, timedelta

import pytest

from app.api.pairing import reset_limits
from app.db.models import PairingRequest

CODE = "FD-4F2A91"


@pytest.fixture(autouse=True)
def fresh_limits():
    reset_limits()
    yield
    reset_limits()


def announce(client, code=CODE):
    return client.post("/devices/pair", json={"code": code})


def poll(client, token, code=CODE):
    return client.post("/devices/pair/credentials", json={"code": code, "poll_token": token})


def claim(client, user, code=CODE, label="Grandma band"):
    return client.post(
        "/devices/claim", json={"code": code, "device_label": label}, headers=user["headers"]
    )


def pair_fully(client, user, code=CODE):
    token = announce(client, code).json()["poll_token"]
    c = claim(client, user, code)
    assert c.status_code == 200, c.text
    p = poll(client, token, code)
    assert p.status_code == 200, p.text
    return token, p.json()


def expire_everything(session_factory):
    db = session_factory()
    try:
        for row in db.query(PairingRequest).all():
            row.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()


def test_full_pairing_flow_gives_working_credentials(client, user_a, predict, set_score):
    a = announce(client)
    assert a.status_code == 200
    token = a.json()["poll_token"]
    waiting = poll(client, token)
    assert waiting.status_code == 202 and waiting.json() == {"status": "waiting"}
    c = claim(client, user_a)
    assert c.status_code == 200
    assert "device_key" not in c.text          # the user never sees the key
    p = poll(client, token)
    assert p.status_code == 200
    body = p.json()
    assert body["status"] == "claimed" and body["device_id"] == c.json()["device_id"]
    set_score(0.5)
    r = predict({"device_id": body["device_id"], "device_key": body["device_key"]})
    assert r.status_code == 200, r.text         # the delivered key really works


def test_credentials_are_returned_only_once(client, user_a):
    token, _ = pair_fully(client, user_a)
    again = poll(client, token)
    assert again.status_code == 410
    assert "device_key" not in again.text


def test_wrong_poll_token_cannot_get_credentials(client, user_a):
    token = announce(client).json()["poll_token"]
    claim(client, user_a)
    bad = poll(client, "not-the-token")
    assert bad.status_code == 404 and "device_key" not in bad.text
    assert poll(client, token).status_code == 200   # the real device still gets them


def test_unknown_code_is_404_for_poll_and_claim(client, user_a):
    assert poll(client, "whatever", code="QQ-123456").status_code == 404
    assert claim(client, user_a, code="QQ-123456").status_code == 404


def test_claim_requires_login(client):
    announce(client)
    r = client.post("/devices/claim", json={"code": CODE, "device_label": "x"})
    assert r.status_code in (401, 403)


def test_code_is_single_use_and_device_belongs_to_claimer(client, user_a, user_b):
    announce(client)
    assert claim(client, user_a).status_code == 200
    assert claim(client, user_b).status_code == 404
    a_labels = [d["device_label"] for d in client.get("/devices", headers=user_a["headers"]).json()]
    b_labels = [d["device_label"] for d in client.get("/devices", headers=user_b["headers"]).json()]
    assert "Grandma band" in a_labels
    assert "Grandma band" not in b_labels


def test_expired_code_cannot_be_claimed_or_polled(client, session_factory, user_a):
    token = announce(client).json()["poll_token"]
    expire_everything(session_factory)
    assert claim(client, user_a).status_code == 404
    assert poll(client, token).status_code == 410


def test_reannounce_replaces_waiting_request(client):
    old = announce(client).json()["poll_token"]
    new = announce(client).json()["poll_token"]
    assert poll(client, old).status_code == 404
    assert poll(client, new).status_code == 202


def test_announce_blocked_while_claimed_but_not_delivered(client, user_a):
    announce(client)
    claim(client, user_a)
    assert announce(client).status_code == 409


def test_device_can_pair_again_after_delivery(client, user_a):
    pair_fully(client, user_a)
    assert announce(client).status_code == 200


@pytest.mark.parametrize("bad", ["", "abc", "FD 4F!2A", "x" * 40, "-ABCDEF-"])
def test_bad_code_format_rejected(client, bad):
    assert announce(client, bad).status_code == 400


@pytest.mark.parametrize("label", ["", "   ", "x" * 101])
def test_bad_label_rejected(client, user_a, label):
    announce(client)
    assert claim(client, user_a, label=label).status_code == 400


def test_claim_attempts_are_rate_limited(client, user_a):
    results = [claim(client, user_a, code=f"ZZ-{i:06d}").status_code for i in range(12)]
    assert results[:10] == [404] * 10
    assert results[10] == 429


def test_announce_is_rate_limited(client):
    results = [announce(client, f"AA-{i:06d}").status_code for i in range(21)]
    assert results[:20] == [200] * 20
    assert results[20] == 429