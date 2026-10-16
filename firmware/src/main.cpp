#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <SD.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>   // v7.x  (PlatformIO: bblanchon/ArduinoJson@^7)
#include <sys/time.h>
#include <time.h>
#include <string.h>
#include <math.h>
#include "driver/gpio.h"
#include "esp_task_wdt.h"

#if __has_include("secrets.h")
#include "secrets.h"
#else
// An unconfigured checkout still builds and continues SD-only acquisition.
#define WIFI_SSID ""
#define WIFI_PASSWORD ""
#define DEVICE_ID ""
#define DEVICE_API_KEY ""
#define BACKEND_PREDICT_URL ""
#endif

// =====================================================================
// MERGED REVISION + TIMING DIAGNOSTICS
//
// Base: continuous-sampling version (loop() only samples; SD logging and the
// /predict POST run in background tasks), plus SD-optional boot, DIAG flags,
// I2C bus recovery, alert/acknowledge handling and checked chunked SD writes.
//
// Added in the previous revision (diagnostics only, no behaviour change):
//   - per-window I2C read time (avg / max, microseconds)
//   - per-window peak accel magnitude (g) and the sample index where it occurs
//
// Added in this revision (SD watchdog hardening):
//   - writeBufferToSD: vTaskDelay(1) inside the SD.exists() walk and after every
//     chunk flush, so IDLE0 can run and reset the task watchdog on a slow card
//   - writeBufferToSD: capture index is saved right after the exists() walk, so a
//     reboot no longer re-walks every existing capture file
//   - writeBufferToSD: chunk buffer 2048 -> 1024 bytes (shorter busy-waits per write)
//   - loop(): any window containing an I2C read slower than 50 ms is discarded,
//     so a stalled read can never produce a fake peak or a false alert
//   - DIAG_NO_IDLE0_WDT: optional, test-only; stops watching IDLE0
//
// Build flags (platformio.ini), all optional:
//   -DSD_SPI_HZ=400000       slower SD clock if the card is still flaky
//   -DI2C_HZ=100000          I2C clock for the MPU
//   -DWIFI_TX_POWER=WIFI_POWER_15dBm   raise Wi-Fi power if the router is far
//   -DDIAG_NO_WIFI           never start Wi-Fi (sensor + SD only)
//   -DDIAG_NO_SD             never touch the SD card (sensor + Wi-Fi only)
//   -DDIAG_NO_IDLE0_WDT      TEST ONLY: unsubscribe IDLE0 from the task watchdog
//   -DMOUNT_ORIENTATION=1    see the mounting table below
//   -DCORE_DEBUG_LEVEL=0     silence Wire.cpp error lines that garble the log
// =====================================================================

#define MPU6050_ADDR       0x68
#define PWR_MGMT_1         0x6B
#define ACCEL_CONFIG       0x1C
#define GYRO_CONFIG        0x1B
#define WHO_AM_I_REG       0x75
#define ACCEL_XOUT_H       0x3B

// ---- I2C pins (ESP32 defaults) ----
#define I2C_SDA_PIN        21
#define I2C_SCL_PIN        22

// ---- SD card (VSPI) ----
#define SD_CS_PIN          5
#define SD_SCK_PIN         18
#define SD_MISO_PIN        19
#define SD_MOSI_PIN        23

// SD SPI clock. Lower is more tolerant of loose jumper wires, but each SD
// busy-wait gets longer (the SD library does not yield while it waits).
#ifndef SD_SPI_HZ
#define SD_SPI_HZ          1000000UL
#endif

// I2C clock for the MPU. 100 kHz is the safest on jumper wires.
#ifndef I2C_HZ
#define I2C_HZ             100000UL
#endif

// Internal pull-up on MISO helps when the line is marginal. 0 = off.
#ifndef SD_MISO_PULLUP
#define SD_MISO_PULLUP     1
#endif

// Wi-Fi transmit power. Lower power = smaller current bursts = fewer supply dips
// on a breadboard without decoupling capacitors. Raise it if the router is far.
#ifndef WIFI_TX_POWER
#define WIFI_TX_POWER      WIFI_POWER_11dBm
#endif

// SD failure handling
const int SD_FAILS_BEFORE_COOLDOWN = 3;   // consecutive failed windows before skipping SD
const int SD_COOLDOWN_WINDOWS      = 5;   // windows to skip SD afterwards

// I2C failure handling
const int MAX_CONSEC_READ_FAILS = 40;     // 40 x 5 ms = 200 ms of dead sensor -> abort window
const int MAX_FAILS_PER_WINDOW  = 25;     // more failed reads than this -> window not sent
const unsigned long MAX_READ_US_PER_WINDOW = 50000UL;  // one read slower than this -> window discarded

// ---- Alert hardware ----
#define BUZZER_PIN         25  // via BC547 transistor driver (base resistor ~1k)
#define BUTTON_PIN         26  // other terminal to GND, uses internal pull-up

// ---- Acknowledgment settings ----
// POST  <base>/alerts/<alert_id>/device-acknowledge
// header X-Device-Key: <DEVICE_API_KEY>, JSON body "{}"
// 200 = success (idempotent). <base> is derived from BACKEND_PREDICT_URL
// (everything before "/predict"), or set BACKEND_ALERTS_BASE_URL in secrets.h.
const char* ACK_PATH_SUFFIX = "/device-acknowledge";
const char* ACK_HTTP_METHOD = "POST";

const unsigned long BUTTON_DEBOUNCE_MS        = 40;
const unsigned long BUZZER_TOGGLE_INTERVAL_MS = 400;
const int           MAX_ACK_ATTEMPTS          = 20;   // HTTP-level failures before giving up on one ack

// After a button press, new alerts arriving within this window are acknowledged
// automatically and do NOT re-sound the buzzer. 0 = disabled (default).
const unsigned long ACK_COOLDOWN_MS = 0;

const float MPU_ACCEL_LSB_PER_G   = 2048.0f;   // nominal, at +/-16g (documentation only)
const float MPU_GYRO_LSB_PER_DPS  = 16.4f;     // nominal, at +/-2000dps

// ---- Accelerometer calibration ----
// corrected_g = (raw - bias) / scale
const float ACCEL_BIAS_X  = 63.0f;
const float ACCEL_SCALE_X = 2044.6f;
const float ACCEL_BIAS_Y  = -26.7f;
const float ACCEL_SCALE_Y = 2011.5f;
const float ACCEL_BIAS_Z  = 206.0f;
const float ACCEL_SCALE_Z = 2049.5f;

// ---- Gyro bias calibration ----
// corrected_dps = raw_dps - bias
const float GYRO_BIAS_X = -0.291f;  // deg/s
const float GYRO_BIAS_Y =  0.360f;  // deg/s
const float GYRO_BIAS_Z = -0.868f;  // deg/s

// SisFall-equivalent conversion constants (validated against the trained StandardScaler).
// SISFALL_GYRO_LSB_PER_DPS is NOT the same as MPU_GYRO_LSB_PER_DPS -- SisFall used an ITG3200.
const float SISFALL_ACCEL_LSB_PER_G  = 256.0f;
const float SISFALL_GYRO_LSB_PER_DPS = 14.375f;

// =====================================================================
// Mounting orientation -> SisFall frame
//
// The model was trained on SisFall, where a person STANDING UPRIGHT has gravity
// on the sensor's -Y axis (acc_y ~ -256 counts). The board is rotated in
// software so that standing still gives acc_y ~ -256 and acc_x, acc_z ~ 0.
//
//   standing reading (raw board axis)   ->  set MOUNT_ORIENTATION to
//   Y ~ -256 (already SisFall-like)         MOUNT_Y_DOWN   (no change)
//   Y ~ +256                                MOUNT_Y_UP
//   Z ~ +256                                MOUNT_Z_UP
//   Z ~ -256                                MOUNT_Z_DOWN
//   X ~ +256                                MOUNT_X_UP
//   X ~ -256                                MOUNT_X_DOWN
//
// Every option is a pure rotation, applied identically to accel and gyro,
// AFTER bias/scale calibration and BEFORE the x256 / x14.375 conversion.
// =====================================================================
#define MOUNT_Y_DOWN 0
#define MOUNT_Y_UP   1
#define MOUNT_Z_UP   2
#define MOUNT_Z_DOWN 3
#define MOUNT_X_UP   4
#define MOUNT_X_DOWN 5

#ifndef MOUNT_ORIENTATION
#define MOUNT_ORIENTATION MOUNT_Y_DOWN
#endif

static inline void rotateToSisFall(float &x, float &y, float &z) {
  const float ox = x, oy = y, oz = z;
#if MOUNT_ORIENTATION == MOUNT_Y_DOWN
  (void)ox; (void)oy; (void)oz;                 // identity
#elif MOUNT_ORIENTATION == MOUNT_Y_UP
  x = -ox; y = -oy; z =  oz;
#elif MOUNT_ORIENTATION == MOUNT_Z_UP
  x =  ox; y = -oz; z =  oy;
#elif MOUNT_ORIENTATION == MOUNT_Z_DOWN
  x =  ox; y =  oz; z = -oy;
#elif MOUNT_ORIENTATION == MOUNT_X_UP
  x =  oy; y = -ox; z =  oz;
#elif MOUNT_ORIENTATION == MOUNT_X_DOWN
  x = -oy; y =  ox; z =  oz;
#else
#error "MOUNT_ORIENTATION must be one of MOUNT_Y_DOWN, MOUNT_Y_UP, MOUNT_Z_UP, MOUNT_Z_DOWN, MOUNT_X_UP, MOUNT_X_DOWN"
#endif
}

static const char* mountName() {
#if MOUNT_ORIENTATION == MOUNT_Y_DOWN
  return "MOUNT_Y_DOWN (no remap)";
#elif MOUNT_ORIENTATION == MOUNT_Y_UP
  return "MOUNT_Y_UP";
#elif MOUNT_ORIENTATION == MOUNT_Z_UP
  return "MOUNT_Z_UP";
#elif MOUNT_ORIENTATION == MOUNT_Z_DOWN
  return "MOUNT_Z_DOWN";
#elif MOUNT_ORIENTATION == MOUNT_X_UP
  return "MOUNT_X_UP";
#else
  return "MOUNT_X_DOWN";
#endif
}

// =====================================================================
// Alert / acknowledgment state
//
//   outstanding : alert_ids raised by the backend, NOT yet acknowledged.
//                 Buzzer sounds while this is non-empty.
//   ackQueue    : alert_ids acknowledged by button, waiting for delivery to
//                 the backend (retried; RAM only, lost on reboot).
//
// One button press acknowledges ALL outstanding alerts.
// A later "adl" response never clears anything -- only the button does.
// =====================================================================
#define MAX_TRACKED_ALERTS 8
#define ALERT_ID_LEN       48

portMUX_TYPE alertMux = portMUX_INITIALIZER_UNLOCKED;

char outstandingIds[MAX_TRACKED_ALERTS][ALERT_ID_LEN];
int  outstandingCount = 0;

char    ackQueueIds[MAX_TRACKED_ALERTS][ALERT_ID_LEN];
uint8_t ackAttempts[MAX_TRACKED_ALERTS];
int     ackQueueCount = 0;

volatile bool alertActive = false;      // true while outstandingCount > 0
unsigned long lastAckMillis = 0;
bool          hasAckedBefore = false;

static bool idInList(char list[][ALERT_ID_LEN], int count, const char* id) {
  for (int i = 0; i < count; i++) {
    if (strncmp(list[i], id, ALERT_ID_LEN) == 0) return true;
  }
  return false;
}

// Caller must hold alertMux.
static void pushAckQueueLocked(const char* id) {
  if (ackQueueCount >= MAX_TRACKED_ALERTS) {
    // Queue full: drop the oldest entry to make room.
    memmove(ackQueueIds[0], ackQueueIds[1], (MAX_TRACKED_ALERTS - 1) * ALERT_ID_LEN);
    memmove(&ackAttempts[0], &ackAttempts[1], MAX_TRACKED_ALERTS - 1);
    ackQueueCount = MAX_TRACKED_ALERTS - 1;
  }
  strncpy(ackQueueIds[ackQueueCount], id, ALERT_ID_LEN - 1);
  ackQueueIds[ackQueueCount][ALERT_ID_LEN - 1] = '\0';
  ackAttempts[ackQueueCount] = 0;
  ackQueueCount++;
}

// Called from netTask when /predict returns a non-null alert_id.
void registerAlert(const char* id) {
  bool duplicate = false;
  bool coveredByCooldown = false;

  portENTER_CRITICAL(&alertMux);
  if (idInList(outstandingIds, outstandingCount, id) ||
      idInList(ackQueueIds, ackQueueCount, id)) {
    duplicate = true;
  } else if (ACK_COOLDOWN_MS > 0 && hasAckedBefore &&
             (millis() - lastAckMillis) < ACK_COOLDOWN_MS) {
    pushAckQueueLocked(id);
    coveredByCooldown = true;
  } else {
    if (outstandingCount >= MAX_TRACKED_ALERTS) {
      memmove(outstandingIds[0], outstandingIds[1], (MAX_TRACKED_ALERTS - 1) * ALERT_ID_LEN);
      outstandingCount = MAX_TRACKED_ALERTS - 1;
    }
    strncpy(outstandingIds[outstandingCount], id, ALERT_ID_LEN - 1);
    outstandingIds[outstandingCount][ALERT_ID_LEN - 1] = '\0';
    outstandingCount++;
    alertActive = true;
  }
  portEXIT_CRITICAL(&alertMux);

  if (duplicate) {
    Serial.print("Alert already tracked, ignoring duplicate: ");
    Serial.println(id);
  } else if (coveredByCooldown) {
    Serial.print("Alert raised inside post-acknowledge cooldown; auto-acknowledging, buzzer stays silent: ");
    Serial.println(id);
  } else {
    Serial.print("ALERT ACTIVE: alert_id=");
    Serial.println(id);
    Serial.println("Buzzer sounding. Press the button to acknowledge.");
  }
}

// Runs inside alertTask when a debounced press is detected.
static void handleButtonPress() {
  int moved = 0;

  portENTER_CRITICAL(&alertMux);
  if (outstandingCount > 0) {
    for (int i = 0; i < outstandingCount; i++) {
      pushAckQueueLocked(outstandingIds[i]);
    }
    moved = outstandingCount;
    outstandingCount = 0;
    alertActive = false;
    lastAckMillis = millis();
    hasAckedBefore = true;
  }
  portEXIT_CRITICAL(&alertMux);

  if (moved > 0) {
    Serial.print("Button pressed: ");
    Serial.print(moved);
    Serial.println(" alert(s) acknowledged locally. Buzzer stopping; ack queued for backend.");
  }
  // A press with no active alert is ignored.
}

// Dedicated FreeRTOS task (core 0). Handles the button (debounced polling) and
// the buzzer pattern independently of everything else, so neither freezes during
// the SD write, Wi-Fi connect or HTTP requests.
void alertTask(void *) {
  bool buzzerOn = false;
  unsigned long lastToggle = 0;

  int rawLast = HIGH;
  int stable  = HIGH;
  unsigned long lastChange = 0;

  for (;;) {
    unsigned long now = millis();

    // ---- debounced button ----
    int raw = digitalRead(BUTTON_PIN);
    if (raw != rawLast) {
      rawLast = raw;
      lastChange = now;
    }
    if ((now - lastChange) >= BUTTON_DEBOUNCE_MS && raw != stable) {
      stable = raw;
      if (stable == LOW) handleButtonPress();   // one press -> one acknowledgment
    }

    // ---- buzzer pattern ----
    if (alertActive) {
      if (now - lastToggle >= BUZZER_TOGGLE_INTERVAL_MS) {
        buzzerOn = !buzzerOn;
        digitalWrite(BUZZER_PIN, buzzerOn ? HIGH : LOW);
        lastToggle = now;
      }
    } else if (buzzerOn) {
      buzzerOn = false;
      digitalWrite(BUZZER_PIN, LOW);
    }

    vTaskDelay(pdMS_TO_TICKS(5));
  }
}

// =====================================================================
// MPU access
// =====================================================================
bool writeRegisterChecked(uint8_t reg, uint8_t value, const char* label) {
  Wire.beginTransmission(MPU6050_ADDR);
  Wire.write(reg);
  Wire.write(value);
  uint8_t status = Wire.endTransmission();
  Serial.print("Write ");
  Serial.print(label);
  Serial.print(" -> status: ");
  Serial.println(status);
  return status == 0;
}

bool readRegisterChecked(uint8_t reg, uint8_t &outVal) {
  Wire.beginTransmission(MPU6050_ADDR);
  Wire.write(reg);
  uint8_t status = Wire.endTransmission(false);
  if (status != 0) return false;
  uint8_t n = Wire.requestFrom((uint8_t)MPU6050_ADDR, (uint8_t)1, (uint8_t)true);
  if (n != 1) return false;
  outVal = Wire.read();
  return true;
}

bool readAccelGyroRaw(int16_t &ax, int16_t &ay, int16_t &az,
                      int16_t &gx, int16_t &gy, int16_t &gz) {
  Wire.beginTransmission(MPU6050_ADDR);
  Wire.write(ACCEL_XOUT_H);
  uint8_t status = Wire.endTransmission(false);
  if (status != 0) return false;
  uint8_t n = Wire.requestFrom((uint8_t)MPU6050_ADDR, (uint8_t)14, (uint8_t)true);
  if (n != 14) return false;
  ax = (Wire.read() << 8) | Wire.read();
  ay = (Wire.read() << 8) | Wire.read();
  az = (Wire.read() << 8) | Wire.read();
  Wire.read(); Wire.read();
  gx = (Wire.read() << 8) | Wire.read();
  gy = (Wire.read() << 8) | Wire.read();
  gz = (Wire.read() << 8) | Wire.read();
  return true;
}

static void i2cBegin() {
  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  Wire.setClock(I2C_HZ);
  Wire.setTimeOut(10);   // ms; a stuck read must not stall sampling for ~170 ms
}

// A loose wire can leave a slave holding SDA low mid-byte. Clocking SCL up to 9
// times lets it finish the byte, then a STOP frees the bus.
static void i2cBusRecover() {
  Wire.end();
  pinMode(I2C_SDA_PIN, INPUT_PULLUP);
  pinMode(I2C_SCL_PIN, OUTPUT);
  digitalWrite(I2C_SCL_PIN, HIGH);
  delayMicroseconds(10);
  for (int i = 0; i < 9; i++) {
    digitalWrite(I2C_SCL_PIN, LOW);  delayMicroseconds(10);
    digitalWrite(I2C_SCL_PIN, HIGH); delayMicroseconds(10);
  }
  // STOP condition: SDA low -> high while SCL is high.
  pinMode(I2C_SDA_PIN, OUTPUT);
  digitalWrite(I2C_SDA_PIN, LOW);  delayMicroseconds(10);
  digitalWrite(I2C_SCL_PIN, HIGH); delayMicroseconds(10);
  digitalWrite(I2C_SDA_PIN, HIGH); delayMicroseconds(10);
  i2cBegin();
}

// Re-checks ACCEL_CONFIG/GYRO_CONFIG. If either register no longer reads back
// 0x18, the sensor has silently reset and needs reconfiguring.
bool verifyAndReapplyConfig() {
  uint8_t accelCfg = 0, gyroCfg = 0;
  bool accelOk = readRegisterChecked(ACCEL_CONFIG, accelCfg);
  bool gyroOk  = readRegisterChecked(GYRO_CONFIG,  gyroCfg);

  if (!accelOk || !gyroOk || accelCfg != 0x18 || gyroCfg != 0x18) {
    Serial.println("!! CONFIG DRIFT DETECTED !!");
    Serial.print("ACCEL_CONFIG read as: 0x"); Serial.println(accelOk ? String(accelCfg, HEX) : "READ FAILED");
    Serial.print("GYRO_CONFIG read as: 0x");  Serial.println(gyroOk  ? String(gyroCfg, HEX)  : "READ FAILED");
    Serial.println("Sensor likely lost power or contact briefly and reset to defaults (loose wire?). Re-applying config...");

    if (!accelOk || !gyroOk) i2cBusRecover();   // bus itself may be stuck

    writeRegisterChecked(PWR_MGMT_1, 0x00, "PWR_MGMT_1 (recovery)");
    delay(50);
    writeRegisterChecked(ACCEL_CONFIG, 0b00011000, "ACCEL_CONFIG (recovery)");
    delay(20);
    writeRegisterChecked(GYRO_CONFIG, 0b00011000, "GYRO_CONFIG (recovery)");
    delay(20);

    readRegisterChecked(ACCEL_CONFIG, accelCfg);
    readRegisterChecked(GYRO_CONFIG,  gyroCfg);
    Serial.print("Post-recovery: ACCEL_CONFIG=0x"); Serial.print(accelCfg, HEX);
    Serial.print(" GYRO_CONFIG=0x"); Serial.println(gyroCfg, HEX);
    Serial.println("Windows sent before this point passed their own end-of-window check and are valid.");
    Serial.println();
    return false;
  }
  return true;
}

bool isConfigValid() {
  uint8_t accelCfg = 0, gyroCfg = 0;
  bool accelOk = readRegisterChecked(ACCEL_CONFIG, accelCfg);
  bool gyroOk  = readRegisterChecked(GYRO_CONFIG,  gyroCfg);
  return accelOk && gyroOk && accelCfg == 0x18 && gyroCfg == 0x18;
}

// =====================================================================
// SD logging (runs only in sdTask after setup)
// =====================================================================
const char* INDEX_STATE_FILE = "/next_index.txt";
int captureIndex = 1;

static bool sdReady     = false;   // card currently mounted
static bool indexLoaded = false;   // capture index read from the card at least once

// SD health tracking.
int sdFailStreak  = 0;   // consecutive windows whose SD write failed
int sdSkipWindows = 0;   // windows still to skip SD for after repeated failures

static bool mountSd() {
  SPI.begin(SD_SCK_PIN, SD_MISO_PIN, SD_MOSI_PIN, SD_CS_PIN);
#if SD_MISO_PULLUP
  gpio_pullup_en((gpio_num_t)SD_MISO_PIN);
#endif
  return SD.begin(SD_CS_PIN, SPI, SD_SPI_HZ);
}

void findNextCaptureIndex() {
  File f = SD.open(INDEX_STATE_FILE, FILE_READ);
  if (f) {
    captureIndex = f.parseInt();
    f.close();
    if (captureIndex < 1) captureIndex = 1;
    Serial.print("Resuming capture index from state file: ");
    Serial.println(captureIndex);
  } else {
    captureIndex = 1;
    Serial.println("No index state file found; starting at capture index 1.");
  }
  indexLoaded = true;
}

void persistCaptureIndex() {
  File f = SD.open(INDEX_STATE_FILE, FILE_WRITE);
  if (f) {
    f.seek(0);
    f.println(captureIndex);
    f.close();
  } else {
    Serial.println("!! Failed to persist capture index state file.");
  }
}

// Called after a failed SD write: unmount and mount again. A loose SD wire that
// glitched the bus often recovers this way without a reboot.
static void recoverSdCard() {
  Serial.println("SD error: re-initializing the card (check the SD wiring/power if this repeats).");
  SD.end();
  sdReady = false;
  delay(50);
  if (mountSd()) {
    sdReady = true;
    Serial.println("SD card re-initialized.");
  } else {
    Serial.println("SD re-init failed; will try again on a later window.");
  }
}

#define CAPTURES_PER_FOLDER 50
// FAT scans a folder linearly, so captures go into a folder per
// CAPTURES_PER_FOLDER files (e.g. /cap0026/capture_01326.csv).
static bool buildCapturePath(char* out, size_t n) {
  static int lastDirIdx = -1;
  char dir[16];
  const int dirIdx = captureIndex / CAPTURES_PER_FOLDER;
  snprintf(dir, sizeof(dir), "/cap%04d", dirIdx);
  if (dirIdx != lastDirIdx) {
    if (!SD.exists(dir) && !SD.mkdir(dir)) {
      Serial.print("!! Could not create SD folder ");
      Serial.println(dir);
      return false;
    }
    lastDirIdx = dirIdx;
  }
  snprintf(out, n, "%s/capture_%05d.csv", dir, captureIndex);
  return true;
}

bool writeBufferToSD(float buffer[][6], int numSamples) {
  // Rows are formatted into RAM and written in ~1 KB blocks. Every write is checked.
  // The SD library busy-waits and does not yield, so this function yields
  // (vTaskDelay(1)) between blocks and during the exists() walk; otherwise a slow
  // card can keep sdTask on core 0 long enough to starve IDLE0 and trip the watchdog.
  const unsigned long t0 = millis();
  char filename[40];
  if (!buildCapturePath(filename, sizeof(filename))) return false;

  // Step past any filename that already exists (only needed once per boot).
  static bool checkedExisting = false;
  if (!checkedExisting) {
    while (SD.exists(filename)) {
      captureIndex++;
      if (!buildCapturePath(filename, sizeof(filename))) return false;
      vTaskDelay(1);           // let IDLE0 run and reset the watchdog
    }
    checkedExisting = true;
    persistCaptureIndex();     // reboots no longer re-walk existing files
  }
  const unsigned long t1 = millis();

  File dataFile = SD.open(filename, FILE_WRITE);
  if (!dataFile) {
    Serial.print("!! Failed to open ");
    Serial.print(filename);
    Serial.println(" for writing.");
    return false;
  }
  const unsigned long t2 = millis();

  static char chunk[1024];   // was 2048
  size_t n = snprintf(chunk, sizeof(chunk), "acc_x,acc_y,acc_z,gyro_x,gyro_y,gyro_z\n");
  for (int i = 0; i < numSamples; i++) {
    if (n > sizeof(chunk) - 96) {            // flush before the buffer can overflow
      if (dataFile.write((const uint8_t*)chunk, n) != n) {
        dataFile.close();
        captureIndex++;   // do not append to the partial file next time
        Serial.println("!! SD write returned a short count.");
        return false;
      }
      n = 0;
      vTaskDelay(1);      // lets IDLE0 run and reset the watchdog
    }
    n += snprintf(chunk + n, sizeof(chunk) - n, "%.2f,%.2f,%.2f,%.2f,%.2f,%.2f\n",
                  buffer[i][0], buffer[i][1], buffer[i][2],
                  buffer[i][3], buffer[i][4], buffer[i][5]);
  }
  if (n > 0 && dataFile.write((const uint8_t*)chunk, n) != n) {
    dataFile.close();
    captureIndex++;
    Serial.println("!! SD write returned a short count.");
    return false;
  }
  const unsigned long t3 = millis();
  dataFile.close();
  const unsigned long t4 = millis();

  Serial.print("Saved ");
  Serial.print(numSamples);
  Serial.print(" samples to ");
  Serial.println(filename);

  captureIndex++;
  if ((captureIndex % 10) == 0) persistCaptureIndex();   // boot skips existing names anyway
  const unsigned long t5 = millis();

  Serial.print("SD timing: exists ");  Serial.print(t1 - t0);
  Serial.print(" ms, open ");          Serial.print(t2 - t1);
  Serial.print(" ms, write ");         Serial.print(t3 - t2);
  Serial.print(" ms, close ");         Serial.print(t4 - t3);
  Serial.print(" ms, index file ");    Serial.print(t5 - t4);
  Serial.println(" ms");
  return true;
}

// =====================================================================
// Network (runs only in netTask after setup; bootNetworkSync runs in setup)
// =====================================================================
bool firmwareNetworkConfigIsSet() {
  return WIFI_SSID[0] != '\0'
      && WIFI_PASSWORD[0] != '\0'
      && DEVICE_ID[0] != '\0'
      && DEVICE_API_KEY[0] != '\0'
      && BACKEND_PREDICT_URL[0] != '\0'
      && strcmp(WIFI_SSID, "YOUR_WIFI_SSID") != 0
      && strcmp(WIFI_PASSWORD, "YOUR_WIFI_PASSWORD") != 0
      && strcmp(DEVICE_ID, "YOUR_DEVICE_UUID") != 0
      && strcmp(DEVICE_API_KEY, "YOUR_DEVICE_KEY") != 0
      && strstr(BACKEND_PREDICT_URL, "<PC-LAN-IP>") == nullptr;
}

bool ensureWifiConnectedOnce() {
#ifdef DIAG_NO_WIFI
  return false;
#else
  static bool warnedNoConfig = false;
  if (!firmwareNetworkConfigIsSet()) {
    if (!warnedNoConfig) {
      Serial.println("Network settings are incomplete (check secrets.h); running SD-only.");
      warnedNoConfig = true;
    }
    return false;
  }

  if (WiFi.status() == WL_CONNECTED) return true;

  Serial.println("Wi-Fi is disconnected; making one bounded connection attempt.");
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);          // no modem power-save: its wake-ups line up with I2C read errors
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  WiFi.setTxPower(WIFI_TX_POWER);   // smaller current bursts (no decoupling capacitors)
  const unsigned long startedAt = millis();
  const unsigned long reconnectTimeoutMs = 5000UL;

  while (millis() - startedAt < reconnectTimeoutMs) {
    if (WiFi.status() == WL_CONNECTED) {
      configTime(0, 0, "pool.ntp.org", "time.nist.gov");
      Serial.println("Wi-Fi connected; UTC clock synchronization requested.");
      return true;
    }
    delay(100);
  }

  Serial.println("Wi-Fi connection attempt timed out; will retry on the next window.");
  return false;
#endif
}

bool formatWindowStartUtc(const timeval &capturedAt, char *output, size_t outputSize) {
  if (capturedAt.tv_sec < 1700000000) return false;

  struct tm utcTime;
  if (gmtime_r(&capturedAt.tv_sec, &utcTime) == nullptr) return false;

  char wholeSeconds[24];
  if (strftime(wholeSeconds, sizeof(wholeSeconds), "%Y-%m-%dT%H:%M:%S", &utcTime) == 0) {
    return false;
  }

  const int written = snprintf(
      output,
      outputSize,
      "%s.%03ldZ",
      wholeSeconds,
      static_cast<long>(capturedAt.tv_usec / 1000)
  );
  return written > 0 && static_cast<size_t>(written) < outputSize;
}

// The request body is built in ONE buffer allocated at boot, before Wi-Fi
// fragments the heap (a String copy risks an empty body -> HTTP 422).
static char* postBody = nullptr;
static const size_t POST_BODY_CAP = 34000;

static size_t buildPredictionRequestInto(char* out, size_t cap, float buffer[][6],
                                         int numSamples, const timeval &windowStart) {
  char timestamp[40];
  if (!formatWindowStartUtc(windowStart, timestamp, sizeof(timestamp))) return 0;

  int head = snprintf(out, cap,
      "{\"device_id\":\"%s\",\"window_start\":\"%s\",\"sample_rate_hz\":200,\"data\":[",
      DEVICE_ID, timestamp);
  if (head <= 0 || (size_t)head >= cap) return 0;
  size_t n = (size_t)head;

  for (int row = 0; row < numSamples; row++) {
    if (n + 96 >= cap) return 0;   // would not fit: send nothing rather than a broken body
    float v[6];
    for (int c = 0; c < 6; c++) v[c] = isfinite(buffer[row][c]) ? buffer[row][c] : 0.0f;
    int w = snprintf(out + n, cap - n, "%s[%.2f,%.2f,%.2f,%.2f,%.2f,%.2f]",
                     row > 0 ? "," : "", v[0], v[1], v[2], v[3], v[4], v[5]);
    if (w <= 0) return 0;
    n += (size_t)w;
  }
  if (n + 4 >= cap) return 0;
  out[n++] = ']';
  out[n++] = '}';
  out[n] = '\0';
  return n;
}

// Parses alert_id from the /predict response with ArduinoJson. Handles a JSON
// string, an integer and null/absent (no alert).
bool parseAlertId(const String &json, char *outId, size_t outSize) {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, json);
  if (err) {
    Serial.print("Could not parse /predict response JSON: ");
    Serial.println(err.c_str());
    return false;
  }

  JsonVariant v = doc["alert_id"];
  if (v.isNull()) return false;

  if (v.is<const char*>()) {
    const char* s = v.as<const char*>();
    if (s == nullptr || s[0] == '\0') return false;
    strncpy(outId, s, outSize - 1);
    outId[outSize - 1] = '\0';
    return true;
  }
  if (v.is<long>()) {
    snprintf(outId, outSize, "%ld", v.as<long>());
    return true;
  }

  Serial.println("alert_id has an unexpected JSON type; no alert registered.");
  return false;
}

// Sends one window to /predict. Independent of the SD result.
void postWindow(float buffer[][6], int numSamples, const timeval &windowStart) {
  if (postBody == nullptr) {
    Serial.println("POST buffer was not allocated at boot; skipping HTTP POST.");
    return;
  }
  const size_t bodyLen = buildPredictionRequestInto(postBody, POST_BODY_CAP, buffer, numSamples, windowStart);
  if (bodyLen == 0) {
    Serial.println("Could not build the request body (clock or size problem); skipping HTTP POST.");
    return;
  }

  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(5000);
  http.setTimeout(10000);
  if (!http.begin(client, BACKEND_PREDICT_URL)) {
    Serial.println("Could not initialize the HTTP client.");
    return;
  }

  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Key", DEVICE_API_KEY);
  const int status = http.POST((uint8_t*)postBody, bodyLen);   // no String copy
  Serial.print("Prediction API HTTP status: ");
  Serial.println(status);

  if (status >= 200 && status < 300) {
    const String response = http.getString();
    Serial.print("Prediction API response: ");
    Serial.println(response);

    char alertId[ALERT_ID_LEN];
    if (parseAlertId(response, alertId, sizeof(alertId))) {
      registerAlert(alertId);
    }
    // A non-alert (e.g. "adl") response intentionally does NOT clear anything.
  } else if (status < 0) {
    Serial.print("Prediction API transport error: ");
    Serial.println(http.errorToString(status));
  } else {
    // Print the backend's reason (first 300 chars). The device key is never printed.
    const String reason = http.getString();
    char ts[40] = "";
    formatWindowStartUtc(windowStart, ts, sizeof(ts));
    Serial.print("Prediction API rejected the request (HTTP ");
    Serial.print(status);
    Serial.print("), window_start sent: ");
    Serial.println(ts);
    Serial.print("Rejection detail: ");
    Serial.println(reason.substring(0, 300));
  }
  http.end();
}

// ---- Acknowledgment delivery ----
enum AckResult { ACK_DELIVERED, ACK_RETRY, ACK_DROP };

String buildAckUrl(const char* alertId) {
#ifdef BACKEND_ALERTS_BASE_URL
  String url = BACKEND_ALERTS_BASE_URL;
#else
  String url = BACKEND_PREDICT_URL;
  int p = url.lastIndexOf("/predict");
  if (p < 0) return String();
  url = url.substring(0, p) + "/alerts";
#endif
  url += "/";
  url += alertId;
  url += ACK_PATH_SUFFIX;
  return url;
}

AckResult sendAckToBackend(const char* alertId) {
  String url = buildAckUrl(alertId);
  if (url.length() == 0) {
    Serial.println("Could not build acknowledge URL (BACKEND_PREDICT_URL has no /predict); dropping ack.");
    return ACK_DROP;
  }

  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(5000);
  http.setTimeout(5000);
  if (!http.begin(client, url)) {
    Serial.println("Could not initialize HTTP client for acknowledgment.");
    return ACK_RETRY;
  }

  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Key", DEVICE_API_KEY);
  const int status = http.sendRequest(ACK_HTTP_METHOD, "{}");

  Serial.print("Acknowledge API (alert_id=");
  Serial.print(alertId);
  Serial.print(") HTTP status: ");
  Serial.println(status);

  AckResult result;
  if ((status >= 200 && status < 300) || status == 409) {
    result = ACK_DELIVERED;
  } else if (status < 0) {
    Serial.print("Acknowledge transport error: ");
    Serial.println(http.errorToString(status));
    result = ACK_RETRY;
  } else if (status >= 500 || status == 408 || status == 429) {
    result = ACK_RETRY;
  } else {
    // 400/401/403/404/422...: retrying cannot fix these. Check route/key/ids.
    Serial.println("Acknowledge rejected by backend (non-retryable). Check route, device key and alert id.");
    result = ACK_DROP;
  }
  http.end();
  return result;
}

// Delivers queued acknowledgments, oldest first. Stops at the first transient
// failure and retries on the next cycle. Does nothing when the queue is empty.
void serviceAckQueue() {
  int pending;
  portENTER_CRITICAL(&alertMux);
  pending = ackQueueCount;
  portEXIT_CRITICAL(&alertMux);
  if (pending == 0) return;

  if (!ensureWifiConnectedOnce()) {
    Serial.print("Acknowledgment pending (");
    Serial.print(pending);
    Serial.println(" queued); no Wi-Fi, will retry next cycle.");
    return;
  }

  for (;;) {
    char id[ALERT_ID_LEN];
    bool have = false;

    portENTER_CRITICAL(&alertMux);
    if (ackQueueCount > 0) {
      strncpy(id, ackQueueIds[0], ALERT_ID_LEN - 1);
      id[ALERT_ID_LEN - 1] = '\0';
      have = true;
    }
    portEXIT_CRITICAL(&alertMux);
    if (!have) break;

    AckResult r = sendAckToBackend(id);

    bool removeFront = false;
    bool stop = false;

    portENTER_CRITICAL(&alertMux);
    if (ackQueueCount > 0 && strncmp(ackQueueIds[0], id, ALERT_ID_LEN) == 0) {
      if (r == ACK_DELIVERED || r == ACK_DROP) {
        removeFront = true;
      } else {
        ackAttempts[0]++;
        if (ackAttempts[0] >= MAX_ACK_ATTEMPTS) removeFront = true;
        stop = true;
      }
      if (removeFront) {
        memmove(ackQueueIds[0], ackQueueIds[1], (MAX_TRACKED_ALERTS - 1) * ALERT_ID_LEN);
        memmove(&ackAttempts[0], &ackAttempts[1], MAX_TRACKED_ALERTS - 1);
        ackQueueCount--;
      }
    }
    portEXIT_CRITICAL(&alertMux);

    if (r == ACK_DELIVERED) {
      Serial.print("Backend acknowledged alert_id=");
      Serial.println(id);
    } else if (removeFront && r == ACK_RETRY) {
      Serial.print("!! Giving up delivering acknowledgment for alert_id=");
      Serial.println(id);
    }
    if (stop) break;
  }
}

// =====================================================================
// Window pool + background tasks
//
//   loop()  (core 1)  samples the MPU at 200 Hz, 512 samples per window, and
//                     never waits for SD or Wi-Fi.
//   sdTask  (core 0)  writes each window to the SD card (a log, never a gate).
//   netTask (core 0)  delivers button acknowledgments, then POSTs each window.
//
// A buffer holds one window and is shared by sdTask and netTask through a
// reference count; it returns to freeQ when both are done with it. If sampling
// needs a buffer and none is free, the oldest queued SD job and then the oldest
// queued POST job is dropped (counted), so sampling is never blocked.
//
// NET_MAX_BACKLOG: if the network is slower than the window rate, netTask skips
// stale windows and posts the newest. Trade-off: a skipped window is never seen
// by the backend.
// =====================================================================
const unsigned long SAMPLE_INTERVAL_US = 5000UL;  // 1,000,000 us / 200 Hz
const int WINDOW_SIZE = 512;                      // 512 samples @ 200 Hz = 2.56 s
#define NBUF 4
const int NET_MAX_BACKLOG = 2;

// SisFall-equivalent counts, channel order: acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z
static float winBufs[NBUF][WINDOW_SIZE][6];
static uint8_t bufRefs[NBUF];
static portMUX_TYPE poolMux = portMUX_INITIALIZER_UNLOCKED;
static QueueHandle_t freeQ = nullptr;
static QueueHandle_t sdQ   = nullptr;
static QueueHandle_t netQ  = nullptr;
static volatile uint32_t droppedSdJobs  = 0;
static volatile uint32_t droppedNetJobs = 0;

struct WindowJob {
  int           idx;            // index into winBufs
  uint32_t      seq;            // window number since boot
  timeval       start;          // UTC at the first sample
  bool          timeOk;         // false if the clock was not synchronised yet
  int           failCount;      // failed sensor reads in this window
  float         elapsedS;
  float         avgIntervalUs;
  float         rateHz;
  float         meanX, meanY, meanZ;
  long          gapUs;          // idle time between the previous window and this one
  unsigned long endMs;          // millis() when the last sample was taken
  // ---- diagnostics ----
  float         readAvgUs;      // average I2C read time in this window
  unsigned long readMaxUs;      // slowest single read in this window
  float         peakSvmG;       // largest accel magnitude in the window (g)
  int           peakIdx;        // sample index of that peak
};

static void releaseBuf(int idx) {
  bool freed = false;
  portENTER_CRITICAL(&poolMux);
  if (bufRefs[idx] > 0 && --bufRefs[idx] == 0) freed = true;
  portEXIT_CRITICAL(&poolMux);
  if (freed) xQueueSend(freeQ, &idx, 0);
}

// Returns a free buffer index, dropping old queued work if it has to. -1 = none.
static int acquireBuf() {
  for (int tries = 0; tries < 3 * NBUF; tries++) {
    int idx;
    if (xQueueReceive(freeQ, &idx, 0) == pdTRUE) return idx;
    WindowJob old;
    if (xQueueReceive(sdQ, &old, 0) == pdTRUE)  { droppedSdJobs++;  releaseBuf(old.idx); continue; }
    if (xQueueReceive(netQ, &old, 0) == pdTRUE) { droppedNetJobs++; releaseBuf(old.idx); continue; }
    vTaskDelay(1);
  }
  return -1;
}

// ---- SD side: window summary + CSV log ----
static void sdProcess(const WindowJob &job) {
  float (*buf)[6] = winBufs[job.idx];

  Serial.println("---- Capture complete ----");
  Serial.print("Window #"); Serial.print(job.seq);
  Serial.print("  Samples: "); Serial.print(WINDOW_SIZE);
  Serial.print("  Failed reads: "); Serial.println(job.failCount);
  Serial.print("Elapsed: "); Serial.print(job.elapsedS, 4); Serial.println(" s");
  Serial.print("Measured avg interval: "); Serial.print(job.avgIntervalUs, 2); Serial.println(" us (target 5000.00 us)");
  Serial.print("Measured sample rate: "); Serial.print(job.rateHz, 2); Serial.println(" Hz (target 200.00 Hz)");
  Serial.print("Gap since previous window: "); Serial.print(job.gapUs); Serial.println(" us (sampling is continuous)");
  Serial.println("First 3 samples, SisFall-equivalent counts [ax, ay, az, gx, gy, gz]:");
  for (int i = 0; i < 3; i++) {
    Serial.print(buf[i][0], 2); Serial.print(", ");
    Serial.print(buf[i][1], 2); Serial.print(", ");
    Serial.print(buf[i][2], 2); Serial.print(", ");
    Serial.print(buf[i][3], 2); Serial.print(", ");
    Serial.print(buf[i][4], 2); Serial.print(", ");
    Serial.println(buf[i][5], 2);
  }
  // Standing still, correctly mounted: acc_y ~ -256, acc_x and acc_z ~ 0.
  Serial.print("Window mean acc [x, y, z]: ");
  Serial.print(job.meanX, 1); Serial.print(", ");
  Serial.print(job.meanY, 1); Serial.print(", ");
  Serial.println(job.meanZ, 1);
  Serial.print("I2C read avg/max: "); Serial.print(job.readAvgUs, 0);
  Serial.print(" / "); Serial.print(job.readMaxUs); Serial.println(" us");
  Serial.print("Peak SVM: "); Serial.print(job.peakSvmG, 2);
  Serial.print(" g at sample "); Serial.println(job.peakIdx);

  const unsigned long sdStartMs = millis();
#ifdef DIAG_NO_SD
  Serial.println("DIAG_NO_SD: SD write skipped.");
#else
  if (sdSkipWindows > 0) {
    sdSkipWindows--;
    Serial.print("SD write skipped (cooling down after repeated failures); windows left to skip: ");
    Serial.println(sdSkipWindows);
  } else {
    // The card may have been missing at boot: try to mount it now.
    if (!sdReady && mountSd()) {
      sdReady = true;
      Serial.println("SD card mounted.");
    }
    if (sdReady && !indexLoaded) findNextCaptureIndex();

    bool savedToSd = sdReady && writeBufferToSD(buf, WINDOW_SIZE);
    if (savedToSd) {
      sdFailStreak = 0;
    } else {
      sdFailStreak++;
      recoverSdCard();
      if (sdFailStreak >= SD_FAILS_BEFORE_COOLDOWN) {
        sdSkipWindows = SD_COOLDOWN_WINDOWS;
        sdFailStreak = 0;
        Serial.print("SD failed repeatedly; skipping SD for the next ");
        Serial.print(SD_COOLDOWN_WINDOWS);
        Serial.println(" windows. Sampling and POST continue.");
      }
    }
  }
#endif
  Serial.print("SD task: window #"); Serial.print(job.seq);
  Serial.print(" took "); Serial.print(millis() - sdStartMs); Serial.print(" ms");
  Serial.print(" (dropped SD jobs so far: "); Serial.print((unsigned long)droppedSdJobs); Serial.println(")");
  Serial.println("=================================");
}

// ---- Network side: acknowledgments first, then /predict ----
static void netProcess(const WindowJob &job) {
  const unsigned long t0 = millis();

  // Always try to get Wi-Fi up, even when this window has no valid timestamp:
  // the NTP clock only syncs once Wi-Fi is connected.
  const bool wifiUp = ensureWifiConnectedOnce();
  if (wifiUp) serviceAckQueue();
  const unsigned long t1 = millis();

  if (!wifiUp) {
    // message already printed by ensureWifiConnectedOnce()
  } else if (!job.timeOk) {
    Serial.println("Window start time is unavailable (clock not synchronised yet); skipping HTTP POST.");
  } else {
    postWindow(winBufs[job.idx], WINDOW_SIZE, job.start);
  }
  const unsigned long t2 = millis();

  Serial.print("Net task: window #"); Serial.print(job.seq);
  Serial.print("  ack "); Serial.print(t1 - t0);
  Serial.print(" ms, POST "); Serial.print(t2 - t1);
  Serial.print(" ms, window end -> done "); Serial.print(t2 - job.endMs);
  Serial.print(" ms (stale windows skipped so far: "); Serial.print((unsigned long)droppedNetJobs);
  Serial.print("), heap free "); Serial.print(ESP.getFreeHeap());
  Serial.print(" largest block "); Serial.println(ESP.getMaxAllocHeap());
}

static void sdTask(void *) {
  for (;;) {
    WindowJob job;
    if (xQueueReceive(sdQ, &job, portMAX_DELAY) != pdTRUE) continue;
    sdProcess(job);
    releaseBuf(job.idx);
  }
}

static void netTask(void *) {
  for (;;) {
    WindowJob job;
    if (xQueueReceive(netQ, &job, portMAX_DELAY) != pdTRUE) continue;
    // Too far behind: skip the stale ones, the newest result matters most.
    while ((int)uxQueueMessagesWaiting(netQ) >= NET_MAX_BACKLOG) {
      WindowJob newer;
      if (xQueueReceive(netQ, &newer, 0) != pdTRUE) break;
      releaseBuf(job.idx);
      droppedNetJobs++;
      job = newer;
    }
    netProcess(job);
    releaseBuf(job.idx);
  }
}

// Connect Wi-Fi and wait for UTC time BEFORE sampling starts, so the first windows
// after power-up can be posted. Continues without network if it cannot connect;
// netTask keeps retrying.
static void bootNetworkSync() {
  if (!firmwareNetworkConfigIsSet()) {
    Serial.println("Network settings are incomplete; running SD-only.");
    return;
  }
  Serial.println("Connecting Wi-Fi and synchronising UTC time before sampling starts...");
  for (int attempt = 0; attempt < 2 && WiFi.status() != WL_CONNECTED; attempt++) {
    ensureWifiConnectedOnce();
  }
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi not connected at boot; the network task will keep retrying.");
    return;
  }
  const unsigned long t0 = millis();
  timeval tv = {};
  while (millis() - t0 < 15000UL) {
    gettimeofday(&tv, nullptr);
    if (tv.tv_sec >= 1700000000) break;
    delay(200);
  }
  if (tv.tv_sec >= 1700000000) {
    Serial.print("UTC time synchronised after ");
    Serial.print(millis() - t0);
    Serial.println(" ms.");
  } else {
    Serial.println("UTC time not available yet; windows are posted once the clock syncs.");
  }
}

static void startBackgroundTasks() {
  freeQ = xQueueCreate(NBUF, sizeof(int));
  sdQ   = xQueueCreate(NBUF, sizeof(WindowJob));
  netQ  = xQueueCreate(NBUF, sizeof(WindowJob));
  if (!freeQ || !sdQ || !netQ) {
    Serial.println("Could not create window queues. Halting.");
    while (true) delay(1000);
  }
  for (int i = 0; i < NBUF; i++) {
    bufRefs[i] = 0;
    xQueueSend(freeQ, &i, 0);
  }
  xTaskCreatePinnedToCore(sdTask,  "sdTask",   8192, nullptr, 1, nullptr, 0);
  xTaskCreatePinnedToCore(netTask, "netTask", 16384, nullptr, 1, nullptr, 0);
}

// =====================================================================
// Setup
// =====================================================================
void setup() {
  Serial.begin(115200);
  delay(1000);

  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);
  pinMode(BUTTON_PIN, INPUT_PULLUP);

  // Start the buzzer/button task first so alerts work for the whole runtime.
  xTaskCreatePinnedToCore(alertTask, "alertTask", 3072, nullptr, 2, nullptr, 0);

  // Short boot chirp: confirms the buzzer path works on every power-up.
  digitalWrite(BUZZER_PIN, HIGH);
  delay(100);
  digitalWrite(BUZZER_PIN, LOW);

  i2cBegin();
  delay(100);

  Serial.println("=================================");
  Serial.println(" MPU6050/6500 -- 200 Hz / 512-sample SisFall-equivalent acquisition");
  Serial.println(" Continuous sampling; SD logging and /predict run in background tasks");
  Serial.println(" Per-axis accel bias/scale + fixed 3-axis gyro bias, both deployed");
  Serial.print(" Mounting orientation: ");
  Serial.println(mountName());
  Serial.print(" SD SPI clock: ");
  Serial.print((unsigned long)SD_SPI_HZ);
  Serial.print(" Hz, I2C clock: ");
  Serial.print((unsigned long)I2C_HZ);
  Serial.println(" Hz");
#ifdef DIAG_NO_WIFI
  Serial.println(" DIAG_NO_WIFI is set: Wi-Fi disabled");
#endif
#ifdef DIAG_NO_SD
  Serial.println(" DIAG_NO_SD is set: SD disabled");
#endif
#ifdef DIAG_NO_IDLE0_WDT
  Serial.println(" DIAG_NO_IDLE0_WDT is set: IDLE0 watchdog disabled (TEST ONLY)");
#endif
  Serial.println("=================================");

  uint8_t whoami;
  if (!readRegisterChecked(WHO_AM_I_REG, whoami)) {
    Serial.println("FAILED to read WHO_AM_I. Check SDA=21, SCL=22, 3.3V, GND. Halting.");
    while (true) delay(1000);
  }
  Serial.print("WHO_AM_I = 0x"); Serial.println(whoami, HEX);

  if (whoami == 0x68) {
    Serial.println("Identity: MPU6050.");
  } else if (whoami == 0x70) {
    Serial.println("Identity: MPU6500 family (register-compatible with MPU6050 at this range;");
    Serial.println("same nominal 2048 LSB/g @ +/-16g and 16.4 LSB/(deg/s) @ +/-2000dps).");
  } else if (whoami == 0x71) {
    Serial.println("Identity: MPU9250 family.");
  } else {
    Serial.println("Identity: unrecognized WHO_AM_I -- verify sensitivity assumptions before trusting conversions.");
  }

  Serial.println("Note: SISFALL_GYRO_LSB_PER_DPS (14.375) differs from MPU_GYRO_LSB_PER_DPS");
  Serial.println("(16.4) -- expected, since SisFall used a different gyro (ITG3200).");

  if (!writeRegisterChecked(PWR_MGMT_1, 0x00, "PWR_MGMT_1")) {
    Serial.println("FAILED to wake device. Halting.");
    while (true) delay(1000);
  }
  delay(100);

  bool ok = true;
  ok &= writeRegisterChecked(ACCEL_CONFIG, 0b00011000, "ACCEL_CONFIG");
  delay(20);
  ok &= writeRegisterChecked(GYRO_CONFIG,  0b00011000, "GYRO_CONFIG");
  delay(20);

  if (!ok) {
    Serial.println("Config write failed. Halting.");
    while (true) delay(1000);
  }

  // ---- SD (optional: the firmware keeps running without it) ----
  Serial.println();
#ifndef DIAG_NO_SD
  Serial.print("Initializing SD card on CS=GPIO");
  Serial.print(SD_CS_PIN);
  Serial.println(" ...");
  for (int attempt = 1; attempt <= 5 && !sdReady; attempt++) {
    sdReady = mountSd();
    if (!sdReady) {
      Serial.print("SD mount attempt "); Serial.print(attempt); Serial.println("/5 failed; retrying.");
      SD.end();
      delay(300);
    }
  }
  if (sdReady) {
    Serial.println("SD card initialized.");
    findNextCaptureIndex();
  } else {
    Serial.println("SD NOT available (check SCK=18, MISO=19, MOSI=23, CS=5, power, FAT32).");
    Serial.println("Continuing without SD: sampling and /predict still run; SD is retried later.");
  }
#endif

  postBody = (char*)malloc(POST_BODY_CAP);
  if (postBody == nullptr) {
    Serial.println("!! Could not allocate the POST buffer; network POSTs disabled.");
  }
  Serial.print("Heap before Wi-Fi: free "); Serial.print(ESP.getFreeHeap());
  Serial.print(" bytes, largest block "); Serial.println(ESP.getMaxAllocHeap());

  bootNetworkSync();
  startBackgroundTasks();

#ifdef DIAG_NO_IDLE0_WDT
  // TEST ONLY: IDLE0 is the only watchdog coverage on core 0. Use this to find out
  // whether the SD write is what trips the watchdog; do not leave it in a real build.
  esp_task_wdt_delete(xTaskGetIdleTaskHandleForCPU(0));
#endif

  Serial.println();
  Serial.println("Setup complete. Beginning continuous 200 Hz / 512-sample capture.");
  Serial.println();
}

// =====================================================================
// 200 Hz / 512-sample continuous acquisition (sampling only)
// =====================================================================
static unsigned long nextSampleTime = 0;
static unsigned long prevWindowEndMicros = 0;
static uint32_t windowSeq = 0;
static float lastGoodRow[6] = {0.0f, -256.0f, 0.0f, 0.0f, 0.0f, 0.0f};

// Discards the window being sampled and re-anchors the sampling schedule.
static void abandonWindow(int bufIdx) {
  prevWindowEndMicros = 0;
  nextSampleTime = 0;
  xQueueSend(freeQ, &bufIdx, 0);   // nobody else holds this buffer
}

void loop() {
  const int bufIdx = acquireBuf();
  if (bufIdx < 0) {
    Serial.println("!! No free window buffer; skipping one window.");
    delay(10);
    return;
  }
  float (*buf)[6] = winBufs[bufIdx];

  timeval windowStartUtc = {};
  bool timeOk = false;
  int sampleCount = 0;
  int failCount = 0;
  int consecFails = 0;
  bool aborted = false;
  unsigned long readSumUs = 0;   // diagnostics: total time spent inside read calls
  unsigned long readMaxUs = 0;   // diagnostics: slowest single read
  // First window, or we fell behind: re-anchor. Otherwise keep the schedule from
  // the previous window so there is no gap between windows.
  if (nextSampleTime == 0 || (long)(micros() - nextSampleTime) > 20000L) {
    nextSampleTime = micros();
  }
  unsigned long captureStartMicros = 0;
  unsigned long captureEndMicros = 0;

  while (sampleCount < WINDOW_SIZE) {
    unsigned long now = micros();
    if ((long)(now - nextSampleTime) < 0) {
      continue;  // not time for the next sample yet -- poll, no delay()
    }
    nextSampleTime += SAMPLE_INTERVAL_US;

    int16_t ax_raw, ay_raw, az_raw, gx_raw, gy_raw, gz_raw;

    const unsigned long r0 = micros();
    const bool rdOk = readAccelGyroRaw(ax_raw, ay_raw, az_raw, gx_raw, gy_raw, gz_raw);
    const unsigned long rd = micros() - r0;
    readSumUs += rd;
    if (rd > readMaxUs) readMaxUs = rd;

    if (rdOk) {
      consecFails = 0;
      float ax_g = (ax_raw - ACCEL_BIAS_X) / ACCEL_SCALE_X;
      float ay_g = (ay_raw - ACCEL_BIAS_Y) / ACCEL_SCALE_Y;
      float az_g = (az_raw - ACCEL_BIAS_Z) / ACCEL_SCALE_Z;

      float gx_dps = (gx_raw / MPU_GYRO_LSB_PER_DPS) - GYRO_BIAS_X;
      float gy_dps = (gy_raw / MPU_GYRO_LSB_PER_DPS) - GYRO_BIAS_Y;
      float gz_dps = (gz_raw / MPU_GYRO_LSB_PER_DPS) - GYRO_BIAS_Z;

      // Board frame -> SisFall frame (same rotation for accel and gyro).
      rotateToSisFall(ax_g, ay_g, az_g);
      rotateToSisFall(gx_dps, gy_dps, gz_dps);

      buf[sampleCount][0] = ax_g * SISFALL_ACCEL_LSB_PER_G;
      buf[sampleCount][1] = ay_g * SISFALL_ACCEL_LSB_PER_G;
      buf[sampleCount][2] = az_g * SISFALL_ACCEL_LSB_PER_G;
      buf[sampleCount][3] = gx_dps * SISFALL_GYRO_LSB_PER_DPS;
      buf[sampleCount][4] = gy_dps * SISFALL_GYRO_LSB_PER_DPS;
      buf[sampleCount][5] = gz_dps * SISFALL_GYRO_LSB_PER_DPS;
      memcpy(lastGoodRow, buf[sampleCount], sizeof(lastGoodRow));
    } else {
      failCount++;
      consecFails++;
      if (consecFails >= MAX_CONSEC_READ_FAILS) {
        aborted = true;
        break;
      }
      // Repeat the previous good sample instead of injecting a false 0 g spike.
      memcpy(buf[sampleCount], lastGoodRow, sizeof(lastGoodRow));
    }

    if (sampleCount == 0) {
      captureStartMicros = now;
      gettimeofday(&windowStartUtc, nullptr);
      timeOk = windowStartUtc.tv_sec >= 1700000000;
    }
    if (sampleCount == WINDOW_SIZE - 1) captureEndMicros = now;

    sampleCount++;
  }

  if (aborted) {
    Serial.println("!! MPU stopped answering mid-window (loose wire?). Recovering the I2C bus; window discarded. !!");
    i2cBusRecover();
    verifyAndReapplyConfig();
    abandonWindow(bufIdx);
    return;
  }

  // A stalled read (SDA/SCL held, supply dip) can leave the sample schedule
  // skewed and produce a fake peak. Discard the whole window instead of sending it.
  if (readMaxUs > MAX_READ_US_PER_WINDOW) {
    Serial.print("!! Slow I2C read in this window (max ");
    Serial.print(readMaxUs);
    Serial.println(" us); window discarded. !!");
    abandonWindow(bufIdx);
    return;
  }

  if (failCount > MAX_FAILS_PER_WINDOW) {
    Serial.print("!! Too many failed sensor reads in this window (");
    Serial.print(failCount);
    Serial.println("); window discarded. Check the MPU wiring. !!");
    abandonWindow(bufIdx);
    return;
  }

  // One config check per window.
  if (!isConfigValid()) {
    Serial.println("!! Config drifted DURING this capture -- these 512 samples are INVALID, discarded. Re-syncing. !!");
    verifyAndReapplyConfig();
    abandonWindow(bufIdx);
    return;
  }

  // ---- statistics (cheap) and hand-off; everything slow happens in the tasks ----
  WindowJob job;
  job.idx = bufIdx;
  job.seq = ++windowSeq;
  job.start = windowStartUtc;
  job.timeOk = timeOk;
  job.failCount = failCount;
  const unsigned long elapsedMicros = captureEndMicros - captureStartMicros;
  job.elapsedS = elapsedMicros / 1000000.0f;
  job.avgIntervalUs = (float)elapsedMicros / (WINDOW_SIZE - 1);
  job.rateHz = (WINDOW_SIZE - 1) / job.elapsedS;
  job.gapUs = (prevWindowEndMicros == 0)
                ? 0L
                : (long)(captureStartMicros - prevWindowEndMicros) - (long)SAMPLE_INTERVAL_US;
  prevWindowEndMicros = captureEndMicros;
  job.endMs = millis();

  float mx = 0, my = 0, mz = 0, peak = 0;
  int peakIdx = 0;
  for (int i = 0; i < WINDOW_SIZE; i++) {
    mx += buf[i][0];
    my += buf[i][1];
    mz += buf[i][2];
    const float svm = sqrtf(buf[i][0] * buf[i][0] +
                            buf[i][1] * buf[i][1] +
                            buf[i][2] * buf[i][2]) / SISFALL_ACCEL_LSB_PER_G;
    if (svm > peak) { peak = svm; peakIdx = i; }
  }
  job.meanX = mx / WINDOW_SIZE;
  job.meanY = my / WINDOW_SIZE;
  job.meanZ = mz / WINDOW_SIZE;
  job.peakSvmG = peak;
  job.peakIdx = peakIdx;
  job.readAvgUs = (float)readSumUs / (float)(sampleCount > 0 ? sampleCount : 1);
  job.readMaxUs = readMaxUs;

  portENTER_CRITICAL(&poolMux);
  bufRefs[bufIdx] = 2;             // one reference each for sdTask and netTask
  portEXIT_CRITICAL(&poolMux);
  if (xQueueSend(sdQ,  &job, 0) != pdTRUE) releaseBuf(bufIdx);
  if (xQueueSend(netQ, &job, 0) != pdTRUE) releaseBuf(bufIdx);
}