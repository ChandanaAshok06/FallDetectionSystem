import { useCallback, useEffect, useMemo, useState } from "react";
import apiClient from "../api/client";
import { forgetDevice, listDevices, rememberDevice } from "../api/devices";
import { Badge, EmptyState, Notice, Skeleton } from "../components/ui";
import { asList, byNewest, formatDateTime, isOpenAlert, timeAgo } from "../utils/format";

function Devices() {
  const [showForm, setShowForm] = useState(false);
  const [deviceLabel, setDeviceLabel] = useState("");
  const [registrationResult, setRegistrationResult] = useState(null);
  const [registering, setRegistering] = useState(false);
  const [formError, setFormError] = useState("");
  const [devices, setDevices] = useState([]);
  const [predictions, setPredictions] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [feedback, setFeedback] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const [fallbackList, setFallbackList] = useState(false);

  const loadDevices = useCallback(async () => {
    try {
      const response = await listDevices();
      setDevices(asList(response.data));
      setFallbackList(Boolean(response.fallback));
      setListError("");
    } catch (error) {
      console.error(error);
      setListError("Failed to load devices.");
    } finally {
      setLoading(false);
    }
  }, []);

  // Last prediction and active alerts are derived from existing endpoints; if they fail the cards simply omit them.
  const loadExtras = useCallback(async () => {
    const [p, a] = await Promise.allSettled([apiClient.get("/predictions"), apiClient.get("/alerts")]);
    if (p.status === "fulfilled") setPredictions(asList(p.value.data));
    if (a.status === "fulfilled") setAlerts(asList(a.value.data));
  }, []);

  useEffect(() => {
    const first = setTimeout(() => {
      loadDevices();
      loadExtras();
    }, 0);
    return () => clearTimeout(first);
  }, [loadDevices, loadExtras]);

  const lastPredictionByDevice = useMemo(() => {
    const map = {};
    byNewest(predictions).forEach((p) => { if (!map[p.device_id]) map[p.device_id] = p; });
    return map;
  }, [predictions]);

  const toggleForm = () => {
    // Closing the form also clears the one-time device key from the screen.
    if (showForm) {
      setRegistrationResult(null);
      setFormError("");
    }
    setShowForm(!showForm);
  };

  const copyText = async (text, what) => {
    try {
      await navigator.clipboard.writeText(text);
      setFeedback({ tone: "success", text: `${what} copied.` });
    } catch {
      setFeedback({ tone: "error", text: "Couldn't copy automatically. Select the text and copy it manually." });
    }
  };

  const handleRegister = async (event) => {
    event.preventDefault();
    setFormError("");
    setRegistrationResult(null);
    setFeedback(null);
    setRegistering(true);

    try {
      const response = await apiClient.post("/devices/register", null, {
        params: {
          device_label: deviceLabel,
        },
      });

      setRegistrationResult(response.data);
      rememberDevice(response.data?.device_id);
      setDeviceLabel("");
      loadDevices();
    } catch (error) {
      console.error(error);
      setFormError("Device registration failed.");
    } finally {
      setRegistering(false);
    }
  };

  const handleDelete = async (device) => {
    if (!window.confirm("Are you sure you want to delete this device?")) {
      return;
    }
    setDeletingId(device.id);
    setFeedback(null);
    try {
      await apiClient.delete(`/devices/${device.id}`);
      forgetDevice(device.id);

      setDevices((currentDevices) =>
        currentDevices.filter((item) => item.id !== device.id)
      );
      setFeedback({ tone: "success", text: `"${device.device_label}" was deleted.` });
    } catch (error) {
      console.error(error);
      const status = error.response?.status;
      setFeedback({
        tone: "error",
        text: status === 404 || status === 405 ? "This backend does not support deleting devices." : "Failed to delete device.",
      });
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <>
      <div className="page-head">
        <p className="page-intro">Manage the devices registered to your caregiver account.</p>
        <div className="toolbar">
          <button type="button" className="btn btn-primary" aria-expanded={showForm} onClick={toggleForm}>
            {showForm ? "Cancel" : "Register Device"}
          </button>
        </div>
      </div>

      <div className="stack">
        {showForm && (
          <section className="card stack" aria-labelledby="register-title">
            <h2 id="register-title">Register New Device</h2>

            <form className="form" onSubmit={handleRegister}>
              <div className="field">
                <label htmlFor="device_label">Device Label</label>
                <input
                  id="device_label"
                  name="device_label"
                  type="text"
                  className="input"
                  maxLength={100}
                  placeholder="e.g. Grandma's Device"
                  value={deviceLabel}
                  onChange={(event) => setDeviceLabel(event.target.value)}
                  required
                />
              </div>
              {formError && <Notice tone="error">{formError}</Notice>}
              <button type="submit" className="btn btn-primary" disabled={registering}>
                {registering ? "Registering..." : "Register"}
              </button>
            </form>

            {registrationResult && (
              <div className="stack" role="status">
                <Notice tone="success">Device Registered Successfully</Notice>
                <div className="secret-box">
                  <span><strong>Device ID:</strong> <span className="mono">{registrationResult.device_id}</span></span>
                  <button type="button" className="btn btn-secondary btn-sm" onClick={() => copyText(registrationResult.device_id, "Device ID")}>Copy</button>
                </div>
                <div className="secret-box">
                  <span><strong>Device Key:</strong> <span className="mono">{registrationResult.device_key}</span></span>
                  <button type="button" className="btn btn-secondary btn-sm" onClick={() => copyText(registrationResult.device_key, "Device key")}>Copy</button>
                </div>
                <Notice tone="warning">
                  Save this device key. It is only returned during registration and disappears when you close this form.
                </Notice>
              </div>
            )}
          </section>
        )}

        {feedback && <Notice tone={feedback.tone}>{feedback.text}</Notice>}
        {fallbackList && (
          <Notice tone="info">
            This backend has no device-list route, so this list shows devices registered from this browser or seen in your predictions and alerts.
          </Notice>
        )}
        {listError && (
          <Notice tone="error" action={<button type="button" className="btn btn-secondary btn-sm" onClick={loadDevices}>Try again</button>}>
            {listError}
          </Notice>
        )}

        <section aria-labelledby="devices-title" className="stack">
          <h2 id="devices-title">Registered Devices</h2>

          {loading ? (
            <div className="two-col" aria-busy="true" aria-label="Loading devices">
              <Skeleton className="skeleton-card" /><Skeleton className="skeleton-card" />
            </div>
          ) : devices.length === 0 ? (
            !listError && (
              <div className="card">
                <EmptyState title="No devices registered yet" action={
                  !showForm && <button type="button" className="btn btn-primary" onClick={() => setShowForm(true)}>Register your first device</button>
                }>
                  Register a wearable to start receiving fall predictions and alerts.
                </EmptyState>
              </div>
            )
          ) : (
            <div className="two-col">
              {devices.map((device) => {
                const last = lastPredictionByDevice[device.id];
                const open = alerts.filter((a) => a.device_id === device.id && isOpenAlert(a.status)).length;
                return (
                  <article key={device.id} className="card stack">
                    <div className="card-head">
                      <h3>{device.device_label}</h3>
                      {open > 0
                        ? <Badge tone="danger" symbol="▲">{open} active alert{open > 1 ? "s" : ""}</Badge>
                        : <Badge tone="success" symbol="✓">No active alerts</Badge>}
                    </div>
                    <dl className="kv">
                      <div><dt>Device ID</dt><dd className="mono">{device.id}</dd></div>
                      <div><dt>Registered</dt><dd>{formatDateTime(device.created_at)}</dd></div>
                      <div><dt>Last prediction</dt><dd>{last ? timeAgo(last.created_at) : "None yet"}</dd></div>
                    </dl>
                    <div className="actions">
                      <button type="button" className="btn btn-secondary btn-sm" onClick={() => copyText(device.id, "Device ID")}>Copy ID</button>
                      <button type="button" className="btn btn-secondary btn-sm" onClick={() => handleDelete(device)} disabled={deletingId === device.id}>
                        {deletingId === device.id ? "Deleting..." : "Delete"}
                      </button>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </>
  );
}

export default Devices;
