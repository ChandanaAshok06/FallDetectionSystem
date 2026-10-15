# SafeStep – Caregiver Frontend

React + Vite frontend for the wearable fall-detection system. It talks to the existing backend API; it contains no backend or hardware code.

## Setup

```bash
npm install
cp .env.example .env      # Windows: copy .env.example .env
npm run dev
```

`.env` needs two values (never commit it):

| Variable | Meaning |
|---|---|
| `VITE_API_BASE_URL` | Address of the backend API |
| `VITE_VAPID_PUBLIC_KEY` | VAPID **public** key used for browser push |

Other scripts: `npm run lint`, `npm run build`, `npm run preview`.

## Pages

`/login` · `/dashboard` · `/devices` · `/predictions` · `/alerts` (all but login require a token). `/register` only explains that registration is not available.

## API calls used

`POST /auth/login` · `GET /devices` · `POST /devices/register` (query param `device_label`) · `DELETE /devices/{id}` · `GET /predictions` · `GET /alerts` · `POST /alerts/{id}/acknowledge` · `POST /alerts/{id}/verify` · `POST /push/subscribe`

The UI shows only fields these calls return. The API does not report live device connection status, so "Last prediction" is derived from the newest prediction.

## Testing notes for hardware / integration

- **Push needs a secure context.** Service workers and push work on `https://` or `http://localhost` only, not on `http://<LAN-IP>`.
- The app polls alerts, predictions and devices every 30 seconds and has a Refresh button.
- If the backend rejects the saved token (HTTP 401) the app signs out and returns to `/login`.

### Checklist (needs a running backend / real device)

- [ ] Login works with a real caregiver account
- [ ] Register a device; confirm the device key is shown once
- [ ] A real prediction appears on Predictions and the Dashboard
- [ ] A fall creates an alert that shows on Dashboard and Alerts (after refresh or 30 s)
- [ ] Acknowledge, then Verify as Fall / False Alarm, update the alert
- [ ] Click "Enable Push Notifications" on the Dashboard, allow the prompt, and confirm a push arrives when an alert is created
- [ ] Clicking the notification opens `/alerts`
- [ ] Deleting a device behaves as expected for its old alerts/predictions (backend behaviour)
