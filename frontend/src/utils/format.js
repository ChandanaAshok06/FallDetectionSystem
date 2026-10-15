export const asList = (value) => (Array.isArray(value) ? value : []);

// The backend timestamps are naive datetimes (datetime.utcnow); values without a timezone are read as UTC.
export function parseDate(value) {
  if (!value) return null;
  const text = String(value);
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/i.test(text);
  const date = new Date(hasZone ? text : `${text}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(value) {
  const date = parseDate(value);
  return date ? date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";
}

export function timeAgo(value) {
  const date = parseDate(value);
  if (!date) return "";
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} d ago`;
}

export const byNewest = (items) =>
  [...items].sort(
    (a, b) => (parseDate(b.created_at)?.getTime() || 0) - (parseDate(a.created_at)?.getTime() || 0)
  );

export const pct = (value, digits = 1) =>
  typeof value === "number" ? `${(value * 100).toFixed(digits)}%` : "—";

export const prettyLabel = (label) =>
  String(label ?? "").toLowerCase() === "adl" ? "normal activity" : String(label ?? "unknown").replace(/_/g, " ");
export const isFallLabel = (label) => String(label ?? "").toLowerCase() === "fall";

// Alert status values set by the backend: active, escalated (no response within the timeout), acknowledged, fall, false_alarm.
// "Open" alerts still need a caregiver response.
export const isOpenAlert = (status) => status === "active" || status === "escalated";

const ALERT_STATUS = {
  active: { label: "Active", tone: "danger", symbol: "▲", next: "Check on the person now, then acknowledge this alert." },
  escalated: { label: "No response", tone: "danger", symbol: "▲", next: "Nobody has responded to this alert yet. Check on the person immediately or call for help, then acknowledge it." },
  acknowledged: { label: "Acknowledged", tone: "warning", symbol: "●", next: "Confirm whether this was a real fall or a false alarm." },
  fall: { label: "Fall confirmed", tone: "info", symbol: "✓", next: "Fall confirmed. Follow your care plan for follow-up." },
  false_alarm: { label: "False alarm", tone: "success", symbol: "✓", next: "Closed as a false alarm. No action needed." },
};

export const alertStatusInfo = (status) =>
  ALERT_STATUS[status] || { label: prettyLabel(status), tone: "neutral", symbol: "●", next: "" };
