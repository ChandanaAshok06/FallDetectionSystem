import { useCallback, useEffect, useMemo, useState } from "react";
import apiClient from "../api/client";
import { listDevices } from "../api/devices";
import { Badge, EmptyState, Notice, Skeleton } from "../components/ui";
import { asList, byNewest, formatDateTime, isFallLabel, pct, prettyLabel, timeAgo } from "../utils/format";

const POLL_MS = 30000;
const PAGE_SIZE = 50;

function Predictions() {
  const [predictions, setPredictions] = useState([]);
  const [devices, setDevices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [refreshing, setRefreshing] = useState(false);
  const [updatedAt, setUpdatedAt] = useState(null);

  const loadAll = useCallback(async () => {
    const [p, d] = await Promise.allSettled([apiClient.get("/predictions"), listDevices()]);
    if (p.status === "fulfilled") {
      setPredictions(asList(p.value.data));
      setError("");
      setUpdatedAt(new Date());
    } else {
      console.error(p.reason);
      setError("Failed to load predictions.");
    }
    if (d.status === "fulfilled") setDevices(asList(d.value.data));
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
  const sorted = useMemo(() => byNewest(predictions), [predictions]);
  const fallCount = sorted.filter((p) => isFallLabel(p.label)).length;
  const filtered = filter === "fall" ? sorted.filter((p) => isFallLabel(p.label)) : sorted;

  return (
    <>
      <div className="page-head">
        <p className="page-intro">View fall detection predictions from connected devices, newest first.</p>
        <div className="toolbar">
          {updatedAt && <span className="updated-at">Updated {timeAgo(updatedAt)}</span>}
          <button type="button" className="btn btn-secondary btn-sm" onClick={handleRefresh} disabled={refreshing || loading}>
            {refreshing ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      <div className="stack">
        <div className="tabs" role="group" aria-label="Filter predictions">
          <button type="button" className="tab" aria-pressed={filter === "all"} onClick={() => { setFilter("all"); setLimit(PAGE_SIZE); }}>
            All ({sorted.length})
          </button>
          <button type="button" className="tab" aria-pressed={filter === "fall"} onClick={() => { setFilter("fall"); setLimit(PAGE_SIZE); }}>
            Falls ({fallCount})
          </button>
        </div>

        {error && (
          <Notice tone="error" action={<button type="button" className="btn btn-secondary btn-sm" onClick={handleRefresh}>Try again</button>}>
            {error}{predictions.length > 0 && " Showing the last data received."}
          </Notice>
        )}

        <section className="card" aria-label="Prediction history">
          {loading ? (
            <div className="stack" aria-busy="true" aria-label="Loading predictions">
              <Skeleton /><Skeleton /><Skeleton /><Skeleton />
            </div>
          ) : filtered.length === 0 ? (
            !error && (
              <EmptyState title={filter === "fall" ? "No fall predictions" : "No predictions yet"}>
                {filter === "fall" ? "No prediction has been labelled as a fall." : "Results appear once a device sends data."}
              </EmptyState>
            )
          ) : (
            <>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Time</th><th>Device</th><th>Label</th><th>Probability</th><th>Threshold</th><th>Model</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.slice(0, limit).map((p) => (
                    <tr key={p.id}>
                      <td data-label="Time">{formatDateTime(p.created_at)}</td>
                      <td data-label="Device">
                        {deviceById[p.device_id]?.device_label || <span className="mono">{p.device_id}</span>}
                      </td>
                      <td data-label="Label">
                        <Badge tone={isFallLabel(p.label) ? "danger" : "success"} symbol={isFallLabel(p.label) ? "▲" : "✓"}>
                          <span className="cap">{prettyLabel(p.label)}</span>
                        </Badge>
                      </td>
                      <td data-label="Probability">
                        <span className="meter">
                          <span className="meter-bar" aria-hidden="true"><i style={{ width: `${Math.min(100, (p.probability || 0) * 100)}%` }} /></span>
                          <span>{pct(p.probability, 2)}</span>
                        </span>
                      </td>
                      <td data-label="Threshold">{pct(p.threshold_used, 2)}</td>
                      <td data-label="Model">{p.model_used}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {filtered.length > limit && (
                <div className="toolbar">
                  <button type="button" className="btn btn-secondary" onClick={() => setLimit((n) => n + PAGE_SIZE)}>
                    Show more ({filtered.length - limit} remaining)
                  </button>
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </>
  );
}

export default Predictions;
