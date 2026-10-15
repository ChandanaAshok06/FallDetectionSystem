# Integration notes (frontend_test checked against backend_share)

Based on the backend source in `backend_share.zip` (app/api/*.py, app/db/models.py, app/core/*.py).

## Routes the frontend uses
| Frontend | Backend route | Notes |
|---|---|---|
| Login | `POST /auth/login` `{caregiver_email, password}` -> `{access_token}` | matches |
| Register (new page) | `POST /auth/register` `{name, caregiver_email, password}` -> `{access_token}` | matches |
| Devices list | `GET /devices` | **not in backend_share**; falls back to devices registered here + ids seen in predictions/alerts, each checked with `GET /devices/{id}` |
| Register device | `POST /devices/register?device_label=` -> `{device_id, device_key}` | matches |
| Delete device | `DELETE /devices/{id}` | **not in backend_share**; the UI shows "not supported" on 404/405 |
| Predictions | `GET /predictions` | fields match (`label` is `fall` or `adl`) |
| Alerts | `GET /alerts` | statuses: `active`, `escalated`, `acknowledged`, `fall`, `false_alarm` |
| Acknowledge / Verify | `POST /alerts/{id}/acknowledge` `{acknowledged_by}`, `POST /alerts/{id}/verify` `{verified_label}` | match |
| Notifications (new panel on Alerts) | `GET /notifications` | `fall_alert`, `wearer_responded`, `escalation` |
| Push | `POST /push/subscribe` | request shape accepted; **no code in backend_share sends push messages** |

## Changes made
- `escalated` alerts (set by the server after ~120 s without a response) are treated as open and urgent everywhere (sidebar count, banner, Alerts tab, Dashboard, Devices).
- Working Register page; "normal activity" label for `adl` predictions; Recent notifications panel.
- Push status now separates: browser permission, browser subscription, saved on server. Failed saves can be retried. Delivery is never claimed.
- Removed debug `console.log` lines; `.env` added to `.gitignore`.
- Approved colour theme (thyme #87A680, white clay #F7E9DE) applied in `src/index.css` only.

## Not changed / not verified
- Backend code is untouched. Live delivery of push, the real ESP32 flow, and `npm run build` on your machine are untested here.
