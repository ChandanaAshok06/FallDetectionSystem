"""Test setup.

The real ensemble module loads TensorFlow models from models_store/ at import time, so it is
replaced with a lightweight stub before the app is imported. Only inference is stubbed; routing,
auth, DB access and threshold logic in the app are the real code.
"""
import os
import sys
import tempfile
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GLOBAL_THRESHOLD = 0.29255566000938416

# main.py runs Base.metadata.create_all() against ./fall_detection.db at import time;
# import it from a scratch directory so the real DB file is never touched.
_scratch = tempfile.mkdtemp(prefix="fds_import_")
_orig_cwd = os.getcwd()
os.chdir(_scratch)

_stub = types.ModuleType("app.inference.ensemble")
_stub.THRESHOLD = GLOBAL_THRESHOLD
_stub.SCORE = 0.5


def _run_ensemble(window):
    return _stub.SCORE


_stub.run_ensemble = _run_ensemble
sys.modules["app.inference.ensemble"] = _stub

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402

os.chdir(_orig_cwd)


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    yield factory
    engine.dispose()


@pytest.fixture()
def client(session_factory):
    def _get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def set_score():
    """Set the probability the stubbed ensemble returns."""
    original = _stub.SCORE

    def _set(value):
        _stub.SCORE = value

    yield _set
    _stub.SCORE = original


def _register(client, email, name):
    r = client.post(
        "/auth/register",
        json={"name": name, "caregiver_email": email, "password": "pw-for-tests-123"},
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    d = client.post("/devices/register", params={"device_label": f"{name}-band"}, headers=headers)
    assert d.status_code == 200, d.text
    return {
        "headers": headers,
        "device_id": d.json()["device_id"],
        "device_key": d.json()["device_key"],
    }


@pytest.fixture()
def user_a(client):
    return _register(client, "a@example.com", "A")


@pytest.fixture()
def user_b(client):
    return _register(client, "b@example.com", "B")


def make_predict(client):
    def _predict(user, impact=False):
        data = [[0.0] * 6 for _ in range(512)]
        if impact:
            data[0][0] = 640.0  # 2.5 g peak (256 counts per g), above the 1.8 g impact gate
        body = {
            "device_id": user["device_id"],
            "window_start": "2026-01-01T00:00:00",
            "sample_rate_hz": 100,
            "data": data,
        }
        return client.post("/predict", json=body, headers={"X-Device-Key": user["device_key"]})

    return _predict


@pytest.fixture()
def predict(client):
    return make_predict(client)


@pytest.fixture(autouse=True)
def isolate_side_effects(monkeypatch):
    """No real notifications or files from alerts in tests; reset the decision layer's memory."""
    import app.api.predict as predict_module
    from app.core import decision

    monkeypatch.setattr(predict_module, "deliver_external", lambda *a, **k: None)
    monkeypatch.setattr(predict_module.personal_store, "save_alert_window", lambda *a, **k: None)
    decision.reset_state()
    yield
    decision.reset_state()
