import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";
import apiClient from "../api/client";
import { isOpenAlert } from "../utils/format";

const navItems = [
  { label: "Dashboard", path: "/dashboard", icon: "▦" },
  { label: "Devices", path: "/devices", icon: "⌁" },
  { label: "Predictions", path: "/predictions", icon: "◉" },
  { label: "Alerts", path: "/alerts", icon: "♧" },
];

function CaregiverLayout() {
  const { logout } = useAuth();
  const location = useLocation();
  // null means "unknown" (not loaded or the request failed); it is never shown as zero.
  const [activeAlerts, setActiveAlerts] = useState(null);

  const currentPage =
    navItems.find((item) => item.path === location.pathname)?.label ||
    "Dashboard";

  useEffect(() => {
    const loadActiveAlerts = async () => {
      try {
        const response = await apiClient.get("/alerts");
        const list = Array.isArray(response.data) ? response.data : [];
        setActiveAlerts(list.filter((alert) => isOpenAlert(alert.status)).length);
      } catch (error) {
        console.error(error);
        setActiveAlerts(null);
      }
    };

    loadActiveAlerts();
    const timer = setInterval(loadActiveAlerts, 30000);
    return () => clearInterval(timer);
  }, []);

  const showBanner = activeAlerts > 0 && location.pathname !== "/alerts" && location.pathname !== "/dashboard";

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-icon">+</div>
          <div>
            <h2>SafeStep</h2>
            <span>Fall Detection System</span>
          </div>
        </div>

        <div className="sidebar-section-label">MAIN MENU</div>

        <nav className="sidebar-nav" aria-label="Main navigation">
          {navItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              title={item.label}
              aria-label={item.label}
              className={({ isActive }) =>
                `sidebar-link${isActive ? " active" : ""}`
              }
            >
              <span className="nav-icon" aria-hidden="true">{item.icon}</span>
              <span className="nav-label">{item.label}</span>
              {item.path === "/alerts" && activeAlerts > 0 && (
                <span className="nav-count nav-label" aria-label={`${activeAlerts} active alerts`}>{activeAlerts}</span>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-bottom">
          <div className="sidebar-note">
            <div className="status-dot" />
            <div>
              <strong>Safety monitoring</strong>
              <span>Dashboard access</span>
            </div>
          </div>

          <button className="logout-button" onClick={logout} title="Logout" aria-label="Logout">
            <span className="nav-icon" aria-hidden="true">↪</span>
            <span className="nav-label">Logout</span>
          </button>
        </div>
      </aside>

      <div className="main-area">
        <header className="topbar">
          <div>
            <p className="topbar-eyebrow">CAREGIVER PORTAL</p>
            <h1>{currentPage}</h1>
          </div>
          <div className="topbar-badge">
            <span className="status-dot" />
            Fall Detection
          </div>
        </header>

        <main className="page-content">
          {showBanner && (
            <div className="alert-banner" role="alert">
              <span aria-hidden="true">▲</span>
              {activeAlerts} active fall alert{activeAlerts > 1 ? "s need" : " needs"} attention.
              <Link to="/alerts">Review alerts</Link>
            </div>
          )}
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export default CaregiverLayout;
