"""The alert card shows the label and confidence of the prediction the alert links to.
That must be the impact window (a fall), not the still window that completes confirmation."""
from app.db.models import Alert, Prediction
from tests.conftest import GLOBAL_THRESHOLD


def _confirmed_alert(predict, user, session_factory):
    """Impact window, then 3 still windows. Returns the stored Alert and the 4 Prediction rows."""
    bodies = [predict(user, impact=True).json()] + [predict(user).json() for _ in range(3)]
    assert bodies[-1]["alert_id"] is not None
    db = session_factory()
    try:
        alert = db.query(Alert).filter(Alert.id == bodies[-1]["alert_id"]).one()
        preds = {b["prediction_id"]: db.query(Prediction).get(b["prediction_id"]) for b in bodies}
        db.expunge_all()
        return alert, preds, bodies
    finally:
        db.close()


def test_alert_links_to_impact_prediction_labelled_fall(predict, user_a, set_score, session_factory):
    set_score(0.9)  # the model flags every window, even the still ones
    alert, preds, bodies = _confirmed_alert(predict, user_a, session_factory)

    assert alert.prediction_id == bodies[0]["prediction_id"]      # impact window, not the last one
    assert alert.prediction_id != bodies[-1]["prediction_id"]
    linked = preds[alert.prediction_id]
    assert linked.label == "fall"
    assert linked.probability >= GLOBAL_THRESHOLD


def test_still_windows_stay_adl_and_are_not_relabelled(predict, user_a, set_score, session_factory):
    set_score(0.9)
    _, preds, bodies = _confirmed_alert(predict, user_a, session_factory)
    for b in bodies[1:]:
        assert preds[b["prediction_id"]].label == "adl"           # raw per-window record unchanged
