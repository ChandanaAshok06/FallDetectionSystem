#pragma once
// Copy this file to secrets.h (same folder). secrets.h is git-ignored; never commit it.
// While any value below still holds its placeholder, the firmware runs SD-only.

#define WIFI_SSID         "YOUR_WIFI_SSID"
#define WIFI_PASSWORD     "YOUR_WIFI_PASSWORD"

// From POST /devices/register (device_id and device_key are returned once).
#define DEVICE_ID         "YOUR_DEVICE_UUID"
#define DEVICE_API_KEY    "YOUR_DEVICE_KEY"

// Your PC's LAN address (run `ipconfig`), same Wi-Fi network as the ESP32.
#define BACKEND_PREDICT_URL "http://<PC-LAN-IP>:8000/predict"

// Optional. By default the acknowledge URL is derived from BACKEND_PREDICT_URL.
// #define BACKEND_ALERTS_BASE_URL "http://<PC-LAN-IP>:8000/alerts"