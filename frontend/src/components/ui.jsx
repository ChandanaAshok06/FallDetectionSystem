export function Badge({ tone = "neutral", symbol, children }) {
  return (
    <span className={`badge badge-${tone}`}>
      {symbol && <span aria-hidden="true">{symbol}</span>}
      {children}
    </span>
  );
}

export function Notice({ tone = "info", children, action }) {
  const role = tone === "error" || tone === "danger" ? "alert" : "status";
  return (
    <div className={`notice notice-${tone}`} role={role}>
      <span>{children}</span>
      {action}
    </div>
  );
}

export function EmptyState({ title, children, action }) {
  return (
    <div className="empty-state">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function Skeleton({ className = "skeleton-line" }) {
  return <div className={`skeleton ${className}`} aria-hidden="true" />;
}
