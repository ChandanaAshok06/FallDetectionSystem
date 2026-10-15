import { useCallback, useEffect, useMemo, useState } from "react";
import apiClient from "../api/client";
import { listDevices } from "../api/devices";
import { Badge, EmptyState, Notice, Skeleton } from "../components/ui";
import { alertStatusInfo, asList, byNewest, formatDateTime, isOpenAlert, pct, prettyLabel, timeAgo } from "../utils/format";

const POLL_MS = 30000;

const FILTERS = [
  { key: "all", label: "All", test: () => true },
  { key: "active", label: "Active", test: (a) => isOpenAlert(a.status) },
  { key: "acknowledged", label: "Acknowledged", test: (a) => a.status === "acknowledged" },
  { key: "resolved", label: "Resolved", test: (a) => a.status === "fall" || a.status === "false_alarm" },
];

// Notification kinds created by the backend (GET /notifications).
const NOTIFICATION_KINDS = {
  fall_alert: { label: "Fall alert", tone: "danger", symbol: "▲" },
  escalation: { label: "No response", tone: "danger", symbol: "▲" },
  wearer_responded: { label: "Wearer responded", tone: "info", symbol: "●" },
};

const VERIFY = {
  fall: { button: "Verify as Fall", confirm: "Confirm that this was a real fall?", yes: "Yes, it was a fall", done: "Alert verified as a fall." },
  false_alarm: { button: "Mark False Alarm", confirm: "Confirm that this was a false alarm?", yes: "Yes, false alarm", done: "Alert marked as a false alarm." },
};

function Alerts() {
  const [alerts, setAlerts] = useState([]);
  const [devices, setDevices] = useState([]);
  const [predictions, setPredictions] = useState([]);
  const [notifications, setNotifications] = useState([]);
  const [loading, setLoading] = useState(true);
  // Loading problems and action problems are kept apart so one failed action never hides the list.
  const [loadError, setLoadError] = useState("");
  const [feedback, setFeedback] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [pending, setPending] = useState(null); // { id, label } while waiting for the user to confirm a verification
  const [filter, setFilter] = useState("all");
  const [refreshing, setRefreshing] = useState(false);
  const [updatedAt, setUpdatedAt] = useState(null);

  const loadAll = useCallback(async () => {
    const [a, d, p, n] = await Promise.allSettled([
      apiClient.get("/alerts"),
      listDevices(),
      apiClient.get("/predictions"),
      apiClient.get("/notifications"),
    ]);
    if (a.status === "fulfilled") {
      setAlerts(asList(a.value.data));
      console.info("[SafeStep] Alerts API response:", {
  count: asList(a.value.data).length,
  alerts: asList(a.value.data).map((item) => ({
    id: item.id,
    device_id: item.device_id,
    status: item.status,
    prediction_id: item.prediction_id,
  })),
});
      setLoadError("");
      setUpdatedAt(new Date());
    } else {
      console.error(a.reason);
      setLoadError("Failed to load alerts.");
    }
    // Device names and confidence are extras; if they fail the page falls back to device IDs.
    if (d.status === "fulfilled") setDevices(asList(d.value.data));
    if (p.status === "fulfilled") setPredictions(asList(p.value.data));
    if (n.status === "fulfilled") setNotifications(asList(n.value.data));
    setLoading(false);
  }, []);

  useEffect(() => {
    const first = setTimeout(loadAll, 0);
    const timer = setInterval(loadAll, POLL_MS);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [loadAll]);

  const handleRefresh = async () => {
    setRefreshing(true);
    await loadAll();
    setRefreshing(false);
  };

  const deviceById = useMemo(() => Object.fromEntries(devices.map((d) => [d.id, d])), [devices]);
  const predictionById = useMemo(() => Object.fromEntries(predictions.map((p) => [p.id, p])), [predictions]);
  const ordered = useMemo(() => {
    const sorted = byNewest(alerts);
    return [...sorted.filter((a) => isOpenAlert(a.status)), ...sorted.filter((a) => !isOpenAlert(a.status))];
  }, [alerts]);

  const currentFilter = FILTERS.find((f) => f.key === filter);
  const visible = ordered.filter(currentFilter.test);

  const runAction = async (alert, request, successText, failureText) => {
    setBusyId(alert.id);
    setFeedback(null);
    try {
      const response = await request();
      setAlerts((currentAlerts) =>
        currentAlerts.map((item) => (item.id === alert.id ? response.data : item))
      );
      setFeedback({ tone: "success", text: successText });
    } catch (error) {
      console.error(error);
      setFeedback({ tone: "error", text: failureText });
    } finally {
      setBusyId(null);
      setPending(null);
    }
  };

  const acknowledge = (alert) =>
    runAction(
      alert,
      () => apiClient.post(`/alerts/${alert.id}/acknowledge`, { acknowledged_by: "Caregiver" }),
      "Alert acknowledged.",
      "Failed to acknowledge alert."
    );

  const verify = (alert, label) =>
    runAction(
      alert,
      () => apiClient.post(`/alerts/${alert.id}/verify`, { verified_label: label }),
      VERIFY[label].done,
      "Failed to verify alert."
    );

  return (
    <>
      <div className="page-head">
        <p className="page-intro">Review and manage fall detection alerts.</p>
        <div className="toolbar">
          {updatedAt && <span className="updated-at">Updated {timeAgo(updatedAt)}</span>}
          <button type="button" className="btn btn-secondary btn-sm" onClick={handleRefresh} disabled={refreshing || loading}>
            {refreshing ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      <div className="stack">
        <div className="tabs" role="group" aria-label="Filter alerts">
          {FILTERS.map((f) => (
            <button key={f.key} type="button" className="tab" aria-pressed={filter === f.key} onClick={() => setFilter(f.key)}>
              {f.label} ({ordered.filter(f.test).length})
            </button>
          ))}
        </div>

        {feedback && <Notice tone={feedback.tone}>{feedback.text}</Notice>}

        {loadError && (
          <Notice tone="error" action={<button type="button" className="btn btn-secondary btn-sm" onClick={handleRefresh}>Try again</button>}>
            {loadError}{alerts.length > 0 && " Showing the last data received."}
          </Notice>
        )}

        {loading ? (
          <div className="stack" aria-busy="true" aria-label="Loading alerts">
            <Skeleton className="skeleton-card" /><Skeleton className="skeleton-card" />
          </div>
        ) : visible.length === 0 ? (
          !loadError && (
            <div className="card">
              <EmptyState title={filter === "all" ? "No alerts yet" : "No alerts in this view"}>
                {filter === "all" ? "When a device detects a possible fall, it will appear here." : "Try a different filter."}
              </EmptyState>
            </div>
          )
        ) : (
          visible.map((alert) => {
            const info = alertStatusInfo(alert.status);
            const prediction = predictionById[alert.prediction_id];
            const device = deviceById[alert.device_id];
            const open = isOpenAlert(alert.status) || alert.status === "acknowledged";
            const busy = busyId === alert.id;
            const verification =
              alert.verified_label === "fall" ? "Confirmed fall"
              : alert.verified_label === "false_alarm" ? "False alarm"
              : "Awaiting verification";

            return (
              <article key={alert.id} className={`alert-card alert-card-${info.tone}`}>
                <div className="alert-top">
                  <Badge tone={info.tone} symbol={info.symbol}>{info.label}</Badge>
                  <span className="muted small">{formatDateTime(alert.created_at)} · {timeAgo(alert.created_at)}</span>
                </div>

                <div>
                  <h3>{device?.device_label || "Unknown device"}</h3>
                  <span className="muted small mono">{alert.device_id}</span>
                </div>

                <dl className="facts">
                  <div>
                    <dt>Prediction</dt>
                    <dd>
                      {prediction ? (
                        <>
                          <span className="cap">{prettyLabel(prediction.label)}</span> · {prediction.model_used}
                        </>
                      ) : "Not available"}
                    </dd>
                  </div>
                  <div>
                    <dt>Model confidence</dt>
                    <dd>
                      {prediction ? (
                        <span className="meter">
                          <span className="meter-bar" aria-hidden="true"><i style={{ width: `${Math.min(100, prediction.probability * 100)}%` }} /></span>
                          <span>{pct(prediction.probability)} (threshold {pct(prediction.threshold_used, 0)})</span>
                        </span>
                      ) : "Not available"}
                    </dd>
                  </div>
                  <div>
                    <dt>Acknowledgement</dt>
                    <dd>
                      {alert.acknowledged_at
                        ? `${alert.acknowledged_by || "Caregiver"} · ${formatDateTime(alert.acknowledged_at)}`
                        : "Not acknowledged"}
                    </dd>
                  </div>
                  <div>
                    <dt>Verification</dt>
                    <dd>{verification}</dd>
                  </div>
                </dl>

                {info.next && <p className="next-step"><strong>Next step:</strong> {info.next}</p>}

                {open && pending?.id === alert.id ? (
                  <div className="confirm-box" role="group" aria-label="Confirm verification">
                    <strong>{VERIFY[pending.label].confirm}</strong>
                    <div className="actions">
                      <button type="button" className={`btn btn-sm ${pending.label === "fall" ? "btn-danger" : "btn-primary"}`}
                        onClick={() => verify(alert, pending.label)} disabled={busy}>
                        {busy ? "Saving..." : VERIFY[pending.label].yes}
                      </button>
                      <button type="button" className="btn btn-secondary btn-sm" onClick={() => setPending(null)} disabled={busy}>Cancel</button>
                    </div>
                  </div>
                ) : (
                  open && (
                    <div className="actions">
                      {isOpenAlert(alert.status) && (
                        <button type="button" className="btn btn-primary" onClick={() => acknowledge(alert)} disabled={busy}>
                          {busy ? "Working..." : "Acknowledge"}
                        </button>
                      )}
                      <button type="button" className="btn btn-secondary" onClick={() => setPending({ id: alert.id, label: "fall" })} disabled={busy}>
                        {VERIFY.fall.button}
                      </button>
                      <button type="button" className="btn btn-secondary" onClick={() => setPending({ id: alert.id, label: "false_alarm" })} disabled={busy}>
                        {VERIFY.false_alarm.button}
                      </button>
                    </div>
                  )
                )}
              </article>
            );
          })
        )}

        {notifications.length > 0 && (
          <section className="card stack" aria-labelledby="notifications-title">
            <h2 id="notifications-title">Recent notifications</h2>
            <ul className="list">
              {notifications.slice(0, 10).map((item) => {
                const kind = NOTIFICATION_KINDS[item.kind] || { label: prettyLabel(item.kind), tone: "neutral", symbol: "●" };
                return (
                  <li key={item.id} className="list-row">
                    <div>
                      <span className="small" style={{ whiteSpace: "pre-line" }}>{item.message}</span>
                      <span className="muted small">{formatDateTime(item.created_at)} · {timeAgo(item.created_at)}</span>
                    </div>
                    <Badge tone={kind.tone} symbol={kind.symbol}>{kind.label}</Badge>
                  </li>
                );
              })}
            </ul>
          </section>
        )}
      </div>
    </>
  );
}

export default Alerts;
