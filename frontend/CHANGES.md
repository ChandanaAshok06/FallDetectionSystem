# What changed (vs frontend_review.zip)

**Not touched:** `src/api/client.js`, `src/routes/ProtectedRoute.jsx`, `src/main.jsx`, `package.json`, `package-lock.json`, `vite.config.js`, `eslint.config.js`. No new dependencies.

## Fixes
| Problem found | Fix | File |
|---|---|---|
| Imports (`./pages/Login`) did not match lowercase file names, so a case-sensitive system could not compile | Files renamed to match the imports: `Login/Dashboard/Devices/Predictions/Alerts.jsx`, `CaregiverLayout.jsx`, `pushNotifications.js` | pages/, layouts/, src/ |
| Dashboard showed "0 active alerts" when the API failed | Each request tracked separately; failures show "Alert status unavailable" and "—" | Dashboard.jsx |
| One failed acknowledge/verify hid the whole alerts list | Load errors and action errors kept apart; list always stays | Alerts.jsx |
| Login crashed on validation errors (`detail` is an array) | Only string errors are shown; otherwise a safe message | Login.jsx |
| Mobile users could not log out (button hidden by CSS) | Labels wrapped in `.nav-label`; logout stays visible as an icon | index.css, CaregiverLayout.jsx |
| Devices errors only visible with the form open | Separate list/form/action messages, always visible | Devices.jsx |
| Device list did not update after registering | List reloads after registration | Devices.jsx |
| Notification icon `/vite.svg` did not exist | New `public/icon-192.png`; `sw.js` uses it | public/sw.js |
| ESLint errors (`clients`, hook export) | `self.clients`; eslint-disable on `useAuth` | sw.js, AuthContext.jsx |
| `/register` was a bare heading | Clear "not available" page | App.jsx |
| Double padding, two `<h1>` per page, pages not styled | Pages use shared classes; one `<h1>` (in the layout) | all pages, index.css |

## Added
- Loading skeletons, empty states, error banners with "Try again", Refresh buttons, 30-second auto-refresh (Dashboard, Alerts, Predictions).
- Alerts: device name, model confidence, acknowledgement and verification details, "next step" text, filters, confirmation before verifying, in-flight button states, success/error messages.
- Predictions: sortable-by-time list, Falls filter, "Show more" (50 at a time), stacked layout on tablet/phone.
- Devices: copy buttons, last prediction (derived), active-alert badge, key cleared when the form closes.
- Dashboard: status banner, active-alert list, recent alerts/predictions, device status, push status.
- Layout: active-alert count on the Alerts link and a banner on other pages.
- Auth: a 401 response (expired token) signs the user out. Remove the `useEffect` in `AuthContext.jsx` to undo.
- Push: `pushNotifications.js` now also exports `isPushSupported()` and `getPushStatus()` (read-only), warns when the page is not HTTPS/localhost, and times out if the service worker never becomes ready. `subscribeToPush()` still posts to `/push/subscribe` exactly as before. Permission is still only requested after the button click.
- `README.md` with setup, API list and a hardware test checklist; `.env.example` (names only).
- Debug `console.log` calls removed (VAPID key info, device response, login).

## Decisions made on your behalf
1. Case fix: files renamed to PascalCase to match imports.
2. `/register`: kept the button, page says registration is not available.
3. Push button stays on the Dashboard (with status).
4. Polling every 30 seconds.
