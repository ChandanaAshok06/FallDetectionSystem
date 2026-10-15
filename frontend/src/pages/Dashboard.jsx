import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";


import apiClient from "../api/client";
import { listDevices } from "../api/devices";
import {
  getPushStatus,
  isPushSupported,
  subscribeToPush,
} from "../pushNotifications";

import { Badge, EmptyState, Notice, Skeleton } from "../components/ui";

import {
  alertStatusInfo,
  asList,
  byNewest,
  formatDateTime,
  isFallLabel,
  isOpenAlert,
  pct,
  prettyLabel,
  timeAgo,
} from "../utils/format";

const POLL_MS = 30000;

function Dashboard() {
  const [devices, setDevices] = useState([]);
  const [predictions, setPredictions] = useState([]);
  const [alerts, setAlerts] = useState([]);

  const [errors, setErrors] = useState({
    devices: false,
    predictions: false,
    alerts: false,
  });

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [updatedAt, setUpdatedAt] = useState(null);

  const [pushState, setPushState] = useState({
    supported: true,
    permission: "default",
    subscribed: false,
  });
  const [pushBusy, setPushBusy] = useState(false);
  const [pushFeedback, setPushFeedback] = useState(null);

  const loadAll = useCallback(async () => {
    const [d, p, a] = await Promise.allSettled([
      listDevices(),
      apiClient.get("/predictions"),
      apiClient.get("/alerts"),
    ]);

    if (d.status === "fulfilled") setDevices(asList(d.value.data));
    if (p.status === "fulfilled") setPredictions(asList(p.value.data));
    if (a.status === "fulfilled") setAlerts(asList(a.value.data));

    [d, p, a].forEach((result) => {
      if (result.status === "rejected") {
        console.error(result.reason);
      }
    });

    setErrors({
      devices: d.status === "rejected",
      predictions: p.status === "rejected",
      alerts: a.status === "rejected",
    });

    setUpdatedAt(new Date());
    setLoading(false);
  }, []);

  const refreshPushStatus = useCallback(async () => {
    setPushState(await getPushStatus());
  }, []);

  useEffect(() => {
    const first = setTimeout(() => {
      loadAll();
      refreshPushStatus();
    }, 0);

    const timer = setInterval(loadAll, POLL_MS);

    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [loadAll, refreshPushStatus]);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await loadAll();
      await refreshPushStatus();
    } finally {
      setRefreshing(false);
    }
  };

  const handleEnablePush = async () => {
    if (!isPushSupported()) {
      setPushFeedback({
        tone: "error",
        text: "Push notifications are not supported by this browser.",
      });
      return;
    }

    setPushBusy(true);
    setPushFeedback(null);

    try {
      const permission = await Notification.requestPermission();

      if (permission !== "granted") {
        throw new Error(
          "Notification permission was not granted. Allow notifications in your browser site settings."
        );
      }

      await subscribeToPush();

      setPushFeedback({
        tone: "success",
        text: "Subscription saved on the server. This app cannot confirm that notifications are delivered.",
      });
    } catch (error) {
      console.error("Push subscription error:", error);
      setPushFeedback({
        tone: "error",
        text: error.message || "Failed to enable notifications.",
      });
    } finally {
      setPushBusy(false);
      await refreshPushStatus();
    }
  };

  const deviceById = useMemo(
    () => Object.fromEntries(devices.map((device) => [device.id, device])),
    [devices]
  );

  const predictionById = useMemo(
    () =>
      Object.fromEntries(
        predictions.map((prediction) => [prediction.id, prediction])
      ),
    [predictions]
  );

  const sortedAlerts = useMemo(() => byNewest(alerts), [alerts]);
  const sortedPredictions = useMemo(
    () => byNewest(predictions),
    [predictions]
  );

  const activeAlerts = sortedAlerts.filter((alert) =>
    isOpenAlert(alert.status)
  );

  const deviceName = (id) =>
    deviceById[id]?.device_label || "Unknown device";

  const failedParts = [
    errors.alerts && "alerts",
    errors.devices && "devices",
    errors.predictions && "predictions",
  ].filter(Boolean);

  const anyError = failedParts.length > 0;

  // Three separate facts: the browser allows notifications, the browser holds a subscription,
  // and the backend confirmed it saved that subscription. Actual delivery is not something this app can observe.
  const pushReady =
    pushState.supported &&
    pushState.permission === "granted" &&
    pushState.subscribed;
  const pushSaved = pushReady && pushState.saved;
  const pushNeedsSave = pushReady && !pushState.saved;

  const recentActivity = useMemo(() => {
    const alertItems = sortedAlerts.slice(0, 5).map((alert) => ({
      id: `alert-${alert.id}`,
      kind: "alert",
      timestamp: alert.created_at,
      deviceId: alert.device_id,
      status: alert.status,
      prediction: predictionById[alert.prediction_id],
    }));

    const predictionItems = sortedPredictions.slice(0, 5).map((prediction) => ({
      id: `prediction-${prediction.id}`,
      kind: "prediction",
      timestamp: prediction.created_at,
      deviceId: prediction.device_id,
      label: prediction.label,
      probability: prediction.probability,
    }));

    return [...alertItems, ...predictionItems]
      .sort(
        (a, b) =>
          new Date(b.timestamp || 0).getTime() -
          new Date(a.timestamp || 0).getTime()
      )
      .slice(0, 8);
  }, [sortedAlerts, sortedPredictions, predictionById]);

  let pushBadge = (
    <Badge tone="warning" symbol="●">
      Not enabled
    </Badge>
  );

  if (!pushState.supported) {
    pushBadge = (
      <Badge tone="neutral" symbol="●">
        Not supported
      </Badge>
    );
  } else if (pushState.permission === "denied") {
    pushBadge = (
      <Badge tone="danger" symbol="✕">
        Blocked
      </Badge>
    );
  } else if (pushSaved) {
    pushBadge = (
      <Badge tone="success" symbol="✓">
        Saved on server
      </Badge>
    );
  } else if (pushNeedsSave) {
    pushBadge = (
      <Badge tone="warning" symbol="●">
        Not saved on server
      </Badge>
    );
  }

  return (
    <>
      <div className="page-head">
        <p className="page-intro">
          Monitor connected devices, fall detections, and alerts from one place.
        </p>

        <div className="toolbar">
          {updatedAt && (
            <span className="updated-at">Updated {timeAgo(updatedAt)}</span>
          )}

          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={handleRefresh}
            disabled={refreshing || loading}
          >
            {refreshing ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      {loading ? (
        <div className="stack" aria-busy="true" aria-label="Loading dashboard">
          <Skeleton className="skeleton-hero" />

          <div className="stat-grid">
            <Skeleton className="skeleton-stat" />
            <Skeleton className="skeleton-stat" />
            <Skeleton className="skeleton-stat" />
          </div>

          <Skeleton className="skeleton-card" />
        </div>
      ) : (
        <div className="stack">
          {anyError && (
            <Notice
              tone="error"
              action={
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={handleRefresh}
                >
                  Try again
                </button>
              }
            >
              Could not load: {failedParts.join(", ")}. Numbers shown as — are
              unknown, not zero.
            </Notice>
          )}

          {errors.alerts && alerts.length === 0 ? (
            <section
              className="status-hero status-hero-neutral"
              aria-labelledby="status-title"
            >
              <span className="status-hero-mark" aria-hidden="true">
                ?
              </span>
              <div>
                <h2 id="status-title">Alert status unavailable</h2>
                <p>
                  Alerts could not be loaded, so we cannot confirm whether
                  there is an active fall alert.
                </p>
              </div>
            </section>
          ) : activeAlerts.length > 0 ? (
            <section
              className="status-hero status-hero-danger"
              aria-labelledby="status-title"
            >
              <span className="status-hero-mark" aria-hidden="true">
                ▲
              </span>
              <div>
                <h2 id="status-title">
                  {activeAlerts.length} active fall alert
                  {activeAlerts.length === 1 ? " needs" : "s need"} attention
                </h2>
                <p>
                  Check on the person first, then open Alerts to acknowledge.
                </p>
                <Link className="card-link" to="/alerts">
                  Review active alerts
                </Link>
              </div>
            </section>
          ) : devices.length === 0 && !errors.devices ? (
            <section
              className="status-hero status-hero-neutral"
              aria-labelledby="status-title"
            >
              <span className="status-hero-mark" aria-hidden="true">
                +
              </span>
              <div>
                <h2 id="status-title">No devices registered yet</h2>
                <p>
                  Register a device to begin monitoring.{" "}
                  <Link className="card-link" to="/devices">
                    Go to Devices
                  </Link>
                </p>
              </div>
            </section>
          ) : (
            <section
              className="status-hero status-hero-ok"
              aria-labelledby="status-title"
            >
              <span className="status-hero-mark" aria-hidden="true">
                ✓
              </span>
              <div>
                <h2 id="status-title">
                  All clear — no active fall alerts
                </h2>
                <p>
                  {errors.devices
                    ? "Device count unavailable."
                    : `${devices.length} registered device${
                        devices.length === 1 ? "" : "s"
                      }.`}
                  {sortedPredictions[0] &&
                    ` Last prediction ${timeAgo(
                      sortedPredictions[0].created_at
                    )}.`}
                </p>
              </div>
            </section>
          )}

          {activeAlerts.length > 0 && (
            <section className="card" aria-labelledby="active-title">
              <div className="card-head">
                <h2 id="active-title">Active alerts</h2>
                <Link className="card-link" to="/alerts">
                  Review and respond
                </Link>
              </div>

              <ul className="list">
                {activeAlerts.slice(0, 5).map((alert) => {
                  const prediction = predictionById[alert.prediction_id];

                  return (
                    <li key={alert.id} className="list-row">
                      <div>
                        <strong>{deviceName(alert.device_id)}</strong>
                        <span className="muted small">
                          {formatDateTime(alert.created_at)} ·{" "}
                          {timeAgo(alert.created_at)}
                          {prediction &&
                            ` · confidence ${pct(prediction.probability)}`}
                        </span>
                      </div>

                      <Badge tone="danger" symbol="▲">
                        Active
                      </Badge>
                    </li>
                  );
                })}
              </ul>
            </section>
          )}

          <div className="stat-grid">
            <div className="card stat-card">
              <div className="stat-card-heading">
                <span
                  className="stat-icon stat-icon-devices"
                  aria-hidden="true"
                >
                  ♧
                </span>
                <p className="stat-label">Devices registered</p>
              </div>

              <p className="stat-value">
                {errors.devices && devices.length === 0 ? "—" : devices.length}
              </p>

              <Link className="card-link" to="/devices">
                Manage devices →
              </Link>
            </div>

            <div className="card stat-card">
              <div className="stat-card-heading">
                <span
                  className="stat-icon stat-icon-predictions"
                  aria-hidden="true"
                >
                  ↗
                </span>
                <p className="stat-label">Predictions</p>
              </div>

              <p className="stat-value">
                {errors.predictions && predictions.length === 0
                  ? "—"
                  : predictions.length}
              </p>

              <Link className="card-link" to="/predictions">
                View history →
              </Link>
            </div>

            <div className="card stat-card">
              <div className="stat-card-heading">
                <span
                  className="stat-icon stat-icon-alerts"
                  aria-hidden="true"
                >
                  !
                </span>
                <p className="stat-label">Active alerts</p>
              </div>

              <p className="stat-value">
                {errors.alerts && alerts.length === 0 ? "—" : activeAlerts.length}
              </p>

              <Link className="card-link" to="/alerts">
                Open alerts →
              </Link>
            </div>
          </div>

          <section className="card" aria-labelledby="recent-activity">
            <div className="card-head">
              <h2 id="recent-activity">Recent Activity</h2>

              <div className="toolbar">
                <Link className="card-link" to="/alerts">
                  All alerts
                </Link>
                <Link className="card-link" to="/predictions">
                  Prediction history
                </Link>
              </div>
            </div>

            {recentActivity.length === 0 ? (
              <EmptyState title="No recent activity">
                Alerts and predictions will appear here when your device starts
                sending data.
              </EmptyState>
            ) : (
              <ul className="list">
                {recentActivity.map((item) => (
                  <li key={item.id} className="list-row">
                    <div>
                      <strong>{deviceName(item.deviceId)}</strong>
                      <span className="muted small">
                        {item.kind === "alert" ? "Alert" : "Prediction"} ·{" "}
                        {formatDateTime(item.timestamp)}
                        {item.kind === "prediction" &&
                          ` · ${pct(item.probability)}`}
                        {item.kind === "alert" &&
                          item.prediction &&
                          ` · confidence ${pct(item.prediction.probability)}`}
                      </span>
                    </div>

                    {item.kind === "alert" ? (
                      <Badge
                        tone={alertStatusInfo(item.status).tone}
                        symbol={alertStatusInfo(item.status).symbol}
                      >
                        {alertStatusInfo(item.status).label}
                      </Badge>
                    ) : (
                      <Badge
                        tone={isFallLabel(item.label) ? "danger" : "success"}
                        symbol={isFallLabel(item.label) ? "▲" : "✓"}
                      >
                        <span className="cap">
                          {prettyLabel(item.label)}
                        </span>
                      </Badge>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="push-summary" aria-labelledby="push-title">
            <div className="push-summary-info">
              <span className="push-summary-icon" aria-hidden="true">
                ♧
              </span>

              <div>
                <h2 id="push-title">Push notifications</h2>
                <p className="muted small">Fall-alert notifications</p>
              </div>
            </div>

            <div className="push-summary-actions">
              {pushBadge}

              {!pushSaved && (
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={handleEnablePush}
                  disabled={
                    pushBusy ||
                    !pushState.supported ||
                    pushState.permission === "denied"
                  }
                >
                  {pushBusy ? (pushNeedsSave ? "Saving..." : "Enabling...") : pushNeedsSave ? "Retry saving" : "Enable"}
                </button>
              )}
            </div>

            {pushState.permission === "denied" && (
              <Notice tone="warning">
                Notifications are blocked. Allow them in your browser settings.
              </Notice>
            )}

            {!isPushSupported() && (
              <Notice tone="warning">
                Push notifications are not supported in this browser.
              </Notice>
            )}

            {pushFeedback && (
              <Notice tone={pushFeedback.tone}>{pushFeedback.text}</Notice>
            )}

            {pushSaved && !pushFeedback && (
              <p className="muted small">
                Subscription saved. Whether a notification arrives depends on the backend sending it; this app cannot confirm delivery.
              </p>
            )}
          </section>
        </div>
      )}
    </>
  );
}

export default Dashboard;