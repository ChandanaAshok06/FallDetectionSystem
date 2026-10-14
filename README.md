# FallDetectionSystem

Personalized fall detection for elderly people: an ESP32 + MPU6500 wearable streams
512-sample windows (200 Hz) to a FastAPI backend that scores them with a CNN-BiLSTM ensemble,
confirms falls with an impact-then-stillness gate, alerts caregivers, and can fine-tune a
personal model from caregiver feedback.

```
wearable (ESP32)  --POST /predict (X-Device-Key)-->  FastAPI backend
 sampling 200 Hz                                       4-model ensemble  ->  decision gate
 SD log, buzzer + button                               alerts, notifications, escalation
 <--- button press: POST /alerts/{id}/device-acknowledge ---
```

## Repository layout
| Path | Contents |
|---|---|
| `firmware/` | PlatformIO project (`src/main.cpp`, `platformio.ini`, `include/secrets.example.h`) |
| `backend/app/` | FastAPI app: `api/` routes, `core/` (decision gate, notify, escalation, auth), `inference/` ensemble, `continual/` personalization |
| `backend/models_store/` | four `.keras` models, `standard_scaler.pkl`, `inference_config.json` (threshold 0.2926) |
| `backend/tools/gate_analysis.py` | replay SD captures through the decision gate |
| `backend/check_sensor_match.py` | compare a capture with the training scaler / SisFall |
| `backend/test_csv_predict.py` | POST one SD capture to `/predict` |
| `scaler_validation/` | check calibrated sensor output against the scaler |

## Backend
```powershell
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Create a caregiver account and a device (the device key is shown once):
```
POST /auth/register   {"name": "...", "caregiver_email": "...", "password": "..."}
POST /devices/register?device_label=Wearable-1        (Bearer token)  ->  device_id, device_key
```
Optional environment variables:
* `JWT_SECRET_KEY` - signing key for caregiver tokens (set this outside local testing)
* `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`; `SMTP_HOST`, `SMTP_FROM`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_SSL`
* `ESCALATION_TIMEOUT_S` (default 120), `NOTIFY_TZ_OFFSET_MIN` (default 330)
* Personalization: `CL_MIN_SAMPLES` (50), `CL_MIN_PER_CLASS` (3), `CL_RETRAIN_EVERY` (10), `CL_MODE` (blend|replace)

### Alert logic (`backend/app/core/decision.py`)
An alert needs an **impact window** (peak |a| >= 1.8 g and model probability >= threshold)
followed by **3 still windows** (peak < 1.35 g). A window with peak >= 1.8 g after an impact
restarts or cancels the candidate; a candidate expires after 90 s. These numbers are defaults
to tune with labelled captures (see `tools/gate_analysis.py`).

## Firmware
1. Copy `firmware/include/secrets.example.h` to `firmware/include/secrets.h` and fill it in.
2. Build and upload with PlatformIO (`upload_port` in `platformio.ini` is machine-specific).
3. Mounting: board orientation `MOUNT_Y_DOWN` (standing upright reads about `[0, -256, 0]` counts).
4. Architecture: `loop()` only samples; `sdTask` writes CSVs to `/capNNNN/`; `netTask` posts windows and
   delivers button acknowledgments.
5. Optional build flags: `-DSD_SPI_HZ=400000`, `-DI2C_HZ=400000`, `-DDIAG_NO_WIFI`, `-DDIAG_NO_SD`.

## Testing a capture without hardware
```powershell
cd backend
python test_csv_predict.py            # pick a capture CSV
python tools/gate_analysis.py D:\path\to\captures
```
