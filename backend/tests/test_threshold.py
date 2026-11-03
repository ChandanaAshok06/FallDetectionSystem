import pytest

from app.db.models import Alert, Prediction, User
from tests.conftest import GLOBAL_THRESHOLD

URL = "/users/me/threshold"


def fall_sequence(predict, user):
    """One impact window, then 3 still windows (what the decision layer needs to confirm a fall).
    Returns (impact_response_body, last_response_body)."""
    first = predict(user, impact=True)
    assert first.status_code == 200, first.text
    last = first
    for _ in range(3):
        last = predict(user)
        assert last.status_code == 200, last.text
    return first.json(), last.json()


def stored_threshold(session_factory, email):
    """Read straight from the DB with a brand-new session (proves persistence)."""
    db = session_factory()
    try:
        return db.query(User).filter(User.caregiver_email == email).one().personalized_threshold
    finally:
        db.close()


# ---------------- authentication ----------------

@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_requires_authentication(client, method):
    kwargs = {"json": {"threshold": 0.4}} if method == "put" else {}
    r = getattr(client, method)(URL, **kwargs)
    # HTTPBearer returns 403 or 401 for a missing header depending on the FastAPI version.
    assert r.status_code in (401, 403)


@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_rejects_invalid_token(client, method):
    kwargs = {"json": {"threshold": 0.4}} if method == "put" else {}
    r = getattr(client, method)(URL, headers={"Authorization": "Bearer not-a-real-token"}, **kwargs)
    assert r.status_code == 401


def test_device_key_cannot_be_used_instead_of_user_token(client, user_a):
    r = client.get(URL, headers={"X-Device-Key": user_a["device_key"]})
    assert r.status_code in (401, 403)


def test_invalid_token_does_not_change_data(client, session_factory, user_a):
    client.put(URL, json={"threshold": 0.4}, headers=user_a["headers"])
    client.delete(URL, headers={"Authorization": "Bearer bad"})
    assert stored_threshold(session_factory, "a@example.com") == 0.4


# ---------------- GET / global fallback ----------------

def test_get_defaults_to_global(client, user_a):
    r = client.get(URL, headers=user_a["headers"])
    assert r.status_code == 200
    assert r.json() == {
        "personalized_threshold": None,
        "global_threshold": GLOBAL_THRESHOLD,
        "effective_threshold": GLOBAL_THRESHOLD,
        "source": "global",
    }


# ---------------- validation ----------------

@pytest.mark.parametrize(
    "raw_body",
    [
        b'{"threshold": true}',
        b'{"threshold": false}',
        b'{"threshold": "0.5"}',
        b'{"threshold": null}',
        b'{"threshold": 0}',
        b'{"threshold": 0.0}',
        b'{"threshold": 1}',
        b'{"threshold": 1.0}',
        b'{"threshold": -0.1}',
        b'{"threshold": 1.5}',
        b'{"threshold": NaN}',
        b'{"threshold": Infinity}',
        b'{"threshold": -Infinity}',
        b'{"threshold": [0.5]}',
        b'{"threshold": {"v": 0.5}}',
        b'{}',
        b'{"threshold": 0.5, "user_id": "someone-else"}',
        b'not json',
    ],
)
def test_invalid_put_rejected_and_nothing_saved(client, session_factory, user_a, raw_body):
    r = client.put(
        URL, content=raw_body, headers={**user_a["headers"], "Content-Type": "application/json"}
    )
    assert r.status_code == 422, (raw_body, r.text)
    assert stored_threshold(session_factory, "a@example.com") is None


@pytest.mark.parametrize("value", [0.15, 0.29255566000938416, 0.5, 0.6])
def test_valid_values_accepted(client, user_a, value):
    r = client.put(URL, json={"threshold": value}, headers=user_a["headers"])
    assert r.status_code == 200
    assert r.json()["personalized_threshold"] == value


# ---------------- persistence ----------------

def test_put_persists_and_get_reflects_it(client, session_factory, user_a):
    r = client.put(URL, json={"threshold": 0.45}, headers=user_a["headers"])
    assert r.status_code == 200
    assert r.json() == {
        "personalized_threshold": 0.45,
        "global_threshold": GLOBAL_THRESHOLD,
        "effective_threshold": 0.45,
        "source": "personalized",
    }
    assert stored_threshold(session_factory, "a@example.com") == 0.45
    g = client.get(URL, headers=user_a["headers"]).json()
    assert g["personalized_threshold"] == 0.45 and g["source"] == "personalized"


def test_put_overwrites_previous_value(client, session_factory, user_a):
    client.put(URL, json={"threshold": 0.45}, headers=user_a["headers"])
    client.put(URL, json={"threshold": 0.6}, headers=user_a["headers"])
    assert stored_threshold(session_factory, "a@example.com") == 0.6


# ---------------- reset ----------------

def test_delete_resets_to_global(client, session_factory, user_a):
    client.put(URL, json={"threshold": 0.45}, headers=user_a["headers"])
    r = client.delete(URL, headers=user_a["headers"])
    assert r.status_code == 200
    assert r.json()["personalized_threshold"] is None
    assert r.json()["source"] == "global"
    assert r.json()["effective_threshold"] == GLOBAL_THRESHOLD
    assert stored_threshold(session_factory, "a@example.com") is None


def test_delete_is_idempotent(client, user_a):
    for _ in range(2):
        r = client.delete(URL, headers=user_a["headers"])
        assert r.status_code == 200 and r.json()["source"] == "global"


# ---------------- predictions ----------------

def test_prediction_uses_global_when_unset(client, session_factory, user_a, predict, set_score):
    set_score(0.5)
    first, last = fall_sequence(predict, user_a)
    assert first["threshold_used"] == GLOBAL_THRESHOLD
    assert first["label"] == "fall"
    assert first["alert_id"] is None  # an alert needs impact + 3 still windows
    assert last["alert_id"] is not None
    db = session_factory()
    try:
        row = db.query(Prediction).filter(Prediction.id == first["prediction_id"]).one()
        assert row.threshold_used == GLOBAL_THRESHOLD
    finally:
        db.close()


def test_prediction_uses_personalized_and_records_it(client, session_factory, user_a, predict, set_score):
    client.put(URL, json={"threshold": 0.6}, headers=user_a["headers"])
    set_score(0.5)  # above global (0.2926) but below personalized (0.6)
    r = predict(user_a, impact=True)
    assert r.status_code == 200
    body = r.json()
    assert body["threshold_used"] == 0.6
    assert body["label"] == "adl"
    assert body["alert_id"] is None
    db = session_factory()
    try:
        row = db.query(Prediction).filter(Prediction.id == body["prediction_id"]).one()
        assert row.threshold_used == 0.6 and row.label == "adl"
        assert db.query(Alert).count() == 0
    finally:
        db.close()


def test_lower_personalized_threshold_triggers_alert(client, session_factory, user_a, predict, set_score):
    client.put(URL, json={"threshold": 0.15}, headers=user_a["headers"])
    set_score(0.2)  # below global (0.2926) but above personalized (0.15)
    first, last = fall_sequence(predict, user_a)
    assert first["threshold_used"] == 0.15
    assert first["label"] == "fall"
    assert first["alert_id"] is None
    assert last["alert_id"] is not None
    db = session_factory()
    try:
        # the alert is created on the window that confirms the fall (the last one)
        assert db.query(Alert).filter(Alert.id == last["alert_id"]).one().prediction_id == last["prediction_id"]
    finally:
        db.close()


def test_score_equal_to_threshold_is_fall(client, user_a, predict, set_score):
    client.put(URL, json={"threshold": 0.4}, headers=user_a["headers"])
    set_score(0.4)
    assert predict(user_a, impact=True).json()["label"] == "fall"  # existing rule: probability >= threshold


def test_prediction_matches_get_effective_threshold(client, user_a, predict, set_score):
    set_score(0.5)
    assert predict(user_a).json()["threshold_used"] == client.get(URL, headers=user_a["headers"]).json()["effective_threshold"]
    client.put(URL, json={"threshold": 0.33}, headers=user_a["headers"])
    assert predict(user_a).json()["threshold_used"] == client.get(URL, headers=user_a["headers"]).json()["effective_threshold"] == 0.33


def test_reset_restores_global_for_predictions(client, user_a, predict, set_score):
    client.put(URL, json={"threshold": 0.6}, headers=user_a["headers"])
    set_score(0.5)
    assert predict(user_a, impact=True).json()["label"] == "adl"
    client.delete(URL, headers=user_a["headers"])
    after = predict(user_a, impact=True).json()
    assert after["threshold_used"] == GLOBAL_THRESHOLD
    assert after["label"] == "fall"

# ---------------- user isolation ----------------

def test_users_thresholds_are_independent(client, session_factory, user_a, user_b):
    client.put(URL, json={"threshold": 0.6}, headers=user_a["headers"])
    b = client.get(URL, headers=user_b["headers"]).json()
    assert b["personalized_threshold"] is None and b["source"] == "global"
    client.put(URL, json={"threshold": 0.2}, headers=user_b["headers"])
    assert client.get(URL, headers=user_a["headers"]).json()["personalized_threshold"] == 0.6
    assert stored_threshold(session_factory, "a@example.com") == 0.6
    assert stored_threshold(session_factory, "b@example.com") == 0.2

def test_delete_only_affects_own_threshold(client, session_factory, user_a, user_b):
    client.put(URL, json={"threshold": 0.5}, headers=user_a["headers"])
    client.put(URL, json={"threshold": 0.2}, headers=user_b["headers"])
    client.delete(URL, headers=user_b["headers"])
    assert stored_threshold(session_factory, "a@example.com") == 0.5
    assert stored_threshold(session_factory, "b@example.com") is None

def test_delete_leaves_other_users_threshold_unchanged(client, session_factory, user_a, user_b):
    client.put(URL, json={"threshold": 0.6}, headers=user_a["headers"])
    client.put(URL, json={"threshold": 0.2}, headers=user_b["headers"])
    client.delete(URL, headers=user_b["headers"])
    assert stored_threshold(session_factory, "a@example.com") == 0.6
    assert stored_threshold(session_factory, "b@example.com") is None

def test_no_way_to_target_another_user(client, session_factory, user_a, user_b):
    db = session_factory()
    try:
        b_id = db.query(User).filter(User.caregiver_email == "b@example.com").one().id
    finally:
        db.close()
    # user_id in the body is rejected, and a user-id path does not exist.
    r = client.put(URL, json={"threshold": 0.9, "user_id": b_id}, headers=user_a["headers"])
    assert r.status_code == 422
    for method in ("get", "put", "delete"):
        kwargs = {"json": {"threshold": 0.9}} if method == "put" else {}
        r = getattr(client, method)(f"/users/{b_id}/threshold", headers=user_a["headers"], **kwargs)
        assert r.status_code in (404, 405)
    assert stored_threshold(session_factory, "b@example.com") is None


def test_predictions_use_each_devices_owner_threshold(client, user_a, user_b, predict, set_score):
    client.put(URL, json={"threshold": 0.6}, headers=user_a["headers"])
    set_score(0.5)
    a = predict(user_a, impact=True).json()
    b = predict(user_b, impact=True).json()
    assert a["threshold_used"] == 0.6 and a["label"] == "adl"
    assert b["threshold_used"] == GLOBAL_THRESHOLD and b["label"] == "fall"


def test_response_never_leaks_other_fields(client, user_a):
    keys = set(client.get(URL, headers=user_a["headers"]).json())
    assert keys == {"personalized_threshold", "global_threshold", "effective_threshold", "source"}

import pytest

@pytest.mark.parametrize("value", [0.1, 0.1499, 0.6001, 0.7, 1.0])
def test_threshold_outside_bounds_rejected(client, user_a, value):
    r = client.put(URL, json={"threshold": value}, headers=user_a["headers"])
    assert r.status_code == 422

@pytest.mark.parametrize("value", [0.15, 0.3, 0.6])
def test_threshold_inside_bounds_accepted(client, user_a, value):
    r = client.put(URL, json={"threshold": value}, headers=user_a["headers"])
    assert r.status_code == 200
    assert r.json()["personalized_threshold"] == value