# Hide device/alert IDs and mask device key (UI only)

Files changed (nothing else touched; backend/API calls unchanged):
- src/pages/Devices.jsx - removed Device ID + Copy from registration result; device key is masked (Show/Hide + Copy); card shows label ("Unnamed device" if empty); Device ID moved into a collapsed "Technical details" section with Copy ID.
- src/pages/Alerts.jsx - removed device_id line; fallback "Unnamed device".
- src/pages/Predictions.jsx - raw device_id fallback replaced with "Unnamed device".
- src/pages/Dashboard.jsx - fallback wording "Unknown device" -> "Unnamed device".
- src/index.css - 3 lines of styling for .tech-details.
