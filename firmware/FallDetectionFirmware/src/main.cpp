#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <SD.h>

#define MPU6050_ADDR       0x68
#define PWR_MGMT_1         0x6B
#define ACCEL_CONFIG       0x1C
#define GYRO_CONFIG        0x1B
#define WHO_AM_I_REG       0x75
#define ACCEL_XOUT_H       0x3B

#define SD_CS_PIN          5   // SD module CS -> GPIO5 (VSPI: SCK=18, MISO=19, MOSI=23)

const float MPU_ACCEL_LSB_PER_G   = 2048.0f;   // nominal, at +/-16g
const float MPU_GYRO_LSB_PER_DPS  = 16.4f;     // nominal, at +/-2000dps

// ---- Accelerometer calibration ----
// Derived from the clean six-orientation calibration test.
// corrected_g = (raw - bias) / scale
const float ACCEL_BIAS_X  = 63.0f;
const float ACCEL_SCALE_X = 2044.6f;
const float ACCEL_BIAS_Y  = -26.7f;
const float ACCEL_SCALE_Y = 2011.5f;
const float ACCEL_BIAS_Z  = 206.0f;
const float ACCEL_SCALE_Z = 2049.5f;

// ---- Gyro bias calibration ----
// Derived from dedicated gyro-bias characterization (6 clean captures across
// 3 orientations, motion-rejected, orientation-independence confirmed).
// corrected_dps = raw_dps - bias  (kept in floating-point deg/s, not quantized
// to raw counts, to avoid unnecessary precision loss)
const float GYRO_BIAS_X = -0.291f;  // deg/s
const float GYRO_BIAS_Y =  0.360f;  // deg/s
const float GYRO_BIAS_Z = -0.868f;  // deg/s

// SisFall-equivalent conversion constants, validated against the trained
// StandardScaler (see scaler_validation/validate_scaler_fit.py). NOTE:
// SISFALL_GYRO_LSB_PER_DPS is NOT the same as MPU_GYRO_LSB_PER_DPS -- SisFall
// was recorded on an ITG3200 gyro with its own sensitivity, so this conversion
// does not cancel to raw MPU counts. Both constants below were confirmed by
// z-scoring converted, calibrated MPU6500 samples against scaler.mean_/scale_.
const float SISFALL_ACCEL_LSB_PER_G  = 256.0f;
const float SISFALL_GYRO_LSB_PER_DPS = 14.375f;

const int NUM_SAMPLES = 300;
const int SAMPLE_DELAY_MS = 5;

// Mid-capture motion rejection threshold: if any single sample during the
// 300-sample capture exceeds this gyro magnitude, the whole batch is discarded.
// (Not used by the current 200 Hz / 512-sample acquisition loop below --
// kept for the earlier calibration-verification workflow if needed again.)
const float CAPTURE_MOTION_THRESHOLD_DPS = 15.0f;

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

bool readRegisterChecked(uint8_t reg, uint8_t &outVal, const char* label) {
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

// ---- SD logging ----
// Scans the card for the first "/capture_NNNNN.csv" that does not already
// exist, so re-flashing/rebooting never silently overwrites earlier captures.
int captureIndex = 1;

void findNextCaptureIndex() {
  char filename[32];
  while (captureIndex < 99999) {
    snprintf(filename, sizeof(filename), "/capture_%05d.csv", captureIndex);
    if (!SD.exists(filename)) break;
    captureIndex++;
  }
}

bool writeBufferToSD(float buffer[][6], int numSamples) {
  char filename[32];
  snprintf(filename, sizeof(filename), "/capture_%05d.csv", captureIndex);

  File dataFile = SD.open(filename, FILE_WRITE);
  if (!dataFile) {
    Serial.print("!! Failed to open ");
    Serial.print(filename);
    Serial.println(" for writing.");
    return false;
  }

  dataFile.println("acc_x,acc_y,acc_z,gyro_x,gyro_y,gyro_z");
  for (int i = 0; i < numSamples; i++) {
    dataFile.print(buffer[i][0], 2); dataFile.print(",");
    dataFile.print(buffer[i][1], 2); dataFile.print(",");
    dataFile.print(buffer[i][2], 2); dataFile.print(",");
    dataFile.print(buffer[i][3], 2); dataFile.print(",");
    dataFile.print(buffer[i][4], 2); dataFile.print(",");
    dataFile.println(buffer[i][5], 2);
  }
  dataFile.close();

  Serial.print("Saved ");
  Serial.print(numSamples);
  Serial.print(" samples to ");
  Serial.println(filename);

  captureIndex++;
  return true;
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  Wire.begin();
  Wire.setClock(100000);
  delay(100);

  Serial.println("=================================");
  Serial.println(" MPU6050/6500 -- 200 Hz / 512-sample SisFall-equivalent acquisition");
  Serial.println(" Per-axis accel bias/scale + fixed 3-axis gyro bias, both deployed");
  Serial.println("=================================");

  uint8_t whoami;
  if (!readRegisterChecked(WHO_AM_I_REG, whoami, "WHO_AM_I")) {
    Serial.println("FAILED to read WHO_AM_I. Halting.");
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
  Serial.println("(16.4) -- this is expected, since SisFall used a different gyro (ITG3200).");
  Serial.println("Gyro bias correction IS applied (fixed 3-axis bias from characterization)");
  Serial.println("before conversion to SisFall-equivalent counts.");

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

  Serial.println();
  Serial.print("Initializing SD card on CS=GPIO");
  Serial.print(SD_CS_PIN);
  Serial.println(" ...");
  if (!SD.begin(SD_CS_PIN)) {
    Serial.println("FAILED to initialize SD card. Check wiring (SCK=18, MISO=19, MOSI=23,");
    Serial.println("CS=5, VCC=3.3V, GND) and that the card is FAT32-formatted. Halting.");
    while (true) delay(1000);
  }
  Serial.println("SD card initialized.");

  findNextCaptureIndex();
  Serial.print("Next capture will be saved as /capture_");
  char idxBuf[6];
  snprintf(idxBuf, sizeof(idxBuf), "%05d", captureIndex);
  Serial.print(idxBuf);
  Serial.println(".csv");

  Serial.println();
  Serial.println("Setup complete. Beginning continuous 200 Hz / 512-sample capture.");
  Serial.println();
}

// Re-checks ACCEL_CONFIG/GYRO_CONFIG before every sample batch. If either register
// no longer reads back 0x18, the sensor has silently reset (e.g. a brief power glitch
// on the breakout, independent of the ESP32's own power) and needs reconfiguring.
bool verifyAndReapplyConfig() {
  uint8_t accelCfg, gyroCfg;
  bool accelOk = readRegisterChecked(ACCEL_CONFIG, accelCfg, "ACCEL_CONFIG check");
  bool gyroOk  = readRegisterChecked(GYRO_CONFIG,  gyroCfg,  "GYRO_CONFIG check");

  if (!accelOk || !gyroOk || accelCfg != 0x18 || gyroCfg != 0x18) {
    Serial.println("!! CONFIG DRIFT DETECTED !!");
    Serial.print("ACCEL_CONFIG read as: 0x"); Serial.println(accelOk ? String(accelCfg, HEX) : "READ FAILED");
    Serial.print("GYRO_CONFIG read as: 0x");  Serial.println(gyroOk  ? String(gyroCfg, HEX)  : "READ FAILED");
    Serial.println("Sensor likely lost power briefly and reset to defaults. Re-applying config...");

    writeRegisterChecked(PWR_MGMT_1, 0x00, "PWR_MGMT_1 (recovery)");
    delay(50);
    writeRegisterChecked(ACCEL_CONFIG, 0b00011000, "ACCEL_CONFIG (recovery)");
    delay(20);
    writeRegisterChecked(GYRO_CONFIG, 0b00011000, "GYRO_CONFIG (recovery)");
    delay(20);

    readRegisterChecked(ACCEL_CONFIG, accelCfg, "ACCEL_CONFIG re-readback");
    readRegisterChecked(GYRO_CONFIG,  gyroCfg,  "GYRO_CONFIG re-readback");
    Serial.print("Post-recovery: ACCEL_CONFIG=0x"); Serial.print(accelCfg, HEX);
    Serial.print(" GYRO_CONFIG=0x"); Serial.println(gyroCfg, HEX);
    Serial.println("Discard the batch that just ran before this message -- it used wrong sensitivity.");
    Serial.println();
    return false; // signal caller to skip/discard the readings from this cycle
  }
  return true;
}

bool isConfigValid() {
  uint8_t accelCfg, gyroCfg;
  bool accelOk = readRegisterChecked(ACCEL_CONFIG, accelCfg, "ACCEL_CONFIG poll");
  bool gyroOk  = readRegisterChecked(GYRO_CONFIG,  gyroCfg,  "GYRO_CONFIG poll");
  return accelOk && gyroOk && accelCfg == 0x18 && gyroCfg == 0x18;
}

// Waits until the gyro is quiet AND periodically re-confirms config hasn't drifted
// mid-wait. A resting sensor reads near-zero gyro counts regardless of which
// sensitivity range is active, so config drift during the wait would NOT trip the
// motion threshold on its own -- it has to be checked for explicitly.
// (Not used by the current 200 Hz / 512-sample acquisition loop below -- kept
// for the earlier calibration-verification workflow if needed again.)
const float GYRO_STABLE_THRESHOLD_DPS = 5.0f;
const int STABLE_SAMPLES_REQUIRED = 20;
const int CONFIG_POLL_INTERVAL = 40; // re-check config every N samples while waiting

bool waitForStable() {
  int quietCount = 0;
  int samplesSinceConfigCheck = 0;
  unsigned long waitStart = millis();

  while (quietCount < STABLE_SAMPLES_REQUIRED) {
    if (millis() - waitStart > 20000) {
      Serial.println("Stillness timeout -- capture cancelled.");
      return false;
    }

    if (samplesSinceConfigCheck >= CONFIG_POLL_INTERVAL) {
      if (!isConfigValid()) {
        Serial.println("Config drifted while waiting for stillness. Re-applying and restarting wait...");
        verifyAndReapplyConfig();
        quietCount = 0;
      }
      samplesSinceConfigCheck = 0;
    }

    int16_t ax, ay, az, gx, gy, gz;
    if (readAccelGyroRaw(ax, ay, az, gx, gy, gz)) {
      float gx_dps = abs(gx / MPU_GYRO_LSB_PER_DPS);
      float gy_dps = abs(gy / MPU_GYRO_LSB_PER_DPS);
      float gz_dps = abs(gz / MPU_GYRO_LSB_PER_DPS);

      if (gx_dps < GYRO_STABLE_THRESHOLD_DPS &&
          gy_dps < GYRO_STABLE_THRESHOLD_DPS &&
          gz_dps < GYRO_STABLE_THRESHOLD_DPS) {
        quietCount++;
      } else {
        quietCount = 0;
      }
    }

    samplesSinceConfigCheck++;
    delay(5);
  }
  return true;
}

// ---- 200 Hz / 512-sample continuous acquisition ----
const unsigned long SAMPLE_INTERVAL_US = 5000UL;  // 1,000,000 us / 200 Hz
const int WINDOW_SIZE = 512;                      // 512 samples @ 200 Hz = 2.56 s

// SisFall-equivalent counts buffer, channel order:
// acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z
float windowBuffer[WINDOW_SIZE][6];

void loop() {
  verifyAndReapplyConfig();

  Serial.println("Capturing 512 samples at 200 Hz...");

  int sampleCount = 0;
  int failCount = 0;
  unsigned long nextSampleTime = micros();
  unsigned long captureStartMicros = 0;
  unsigned long captureEndMicros = 0;

  while (sampleCount < WINDOW_SIZE) {
    unsigned long now = micros();
    if ((long)(now - nextSampleTime) < 0) {
      continue;  // not time for the next sample yet -- poll, no delay()
    }
    nextSampleTime += SAMPLE_INTERVAL_US;

    int16_t ax_raw, ay_raw, az_raw, gx_raw, gy_raw, gz_raw;
    float ax_g, ay_g, az_g, gx_dps, gy_dps, gz_dps;

    if (readAccelGyroRaw(ax_raw, ay_raw, az_raw, gx_raw, gy_raw, gz_raw)) {
      // Existing calibration, applied unchanged, per-sample instead of averaged
      ax_g = (ax_raw - ACCEL_BIAS_X) / ACCEL_SCALE_X;
      ay_g = (ay_raw - ACCEL_BIAS_Y) / ACCEL_SCALE_Y;
      az_g = (az_raw - ACCEL_BIAS_Z) / ACCEL_SCALE_Z;

      gx_dps = (gx_raw / MPU_GYRO_LSB_PER_DPS) - GYRO_BIAS_X;
      gy_dps = (gy_raw / MPU_GYRO_LSB_PER_DPS) - GYRO_BIAS_Y;
      gz_dps = (gz_raw / MPU_GYRO_LSB_PER_DPS) - GYRO_BIAS_Z;
    } else {
      failCount++;
      ax_g = ay_g = az_g = 0.0f;
      gx_dps = gy_dps = gz_dps = 0.0f;
    }

    // Convert to SisFall-equivalent counts (validated constants)
    windowBuffer[sampleCount][0] = ax_g * SISFALL_ACCEL_LSB_PER_G;
    windowBuffer[sampleCount][1] = ay_g * SISFALL_ACCEL_LSB_PER_G;
    windowBuffer[sampleCount][2] = az_g * SISFALL_ACCEL_LSB_PER_G;
    windowBuffer[sampleCount][3] = gx_dps * SISFALL_GYRO_LSB_PER_DPS;
    windowBuffer[sampleCount][4] = gy_dps * SISFALL_GYRO_LSB_PER_DPS;
    windowBuffer[sampleCount][5] = gz_dps * SISFALL_GYRO_LSB_PER_DPS;

    if (sampleCount == 0) captureStartMicros = now;
    if (sampleCount == WINDOW_SIZE - 1) captureEndMicros = now;

    sampleCount++;
  }

  if (!isConfigValid()) {
    Serial.println("!! Config drifted DURING this capture -- these 512 samples are INVALID, discard. Re-syncing for next cycle. !!");
    verifyAndReapplyConfig();
    Serial.println();
    return;
  }

  unsigned long elapsedMicros = captureEndMicros - captureStartMicros;
  float elapsedSeconds = elapsedMicros / 1000000.0f;
  float avgIntervalUs = (float)elapsedMicros / (WINDOW_SIZE - 1);
  float measuredRateHz = (WINDOW_SIZE - 1) / elapsedSeconds;

  Serial.println("---- Capture complete ----");
  Serial.print("Samples: "); Serial.print(WINDOW_SIZE);
  Serial.print("  Failed reads: "); Serial.println(failCount);
  Serial.print("Elapsed: "); Serial.print(elapsedSeconds, 4); Serial.println(" s");
  Serial.print("Measured avg interval: "); Serial.print(avgIntervalUs, 2); Serial.println(" us (target 5000.00 us)");
  Serial.print("Measured sample rate: "); Serial.print(measuredRateHz, 2); Serial.println(" Hz (target 200.00 Hz)");

  Serial.println("First 3 samples, SisFall-equivalent counts [ax, ay, az, gx, gy, gz]:");
  for (int i = 0; i < 3; i++) {
    Serial.print(windowBuffer[i][0], 2); Serial.print(", ");
    Serial.print(windowBuffer[i][1], 2); Serial.print(", ");
    Serial.print(windowBuffer[i][2], 2); Serial.print(", ");
    Serial.print(windowBuffer[i][3], 2); Serial.print(", ");
    Serial.print(windowBuffer[i][4], 2); Serial.print(", ");
    Serial.println(windowBuffer[i][5], 2);
  }

  writeBufferToSD(windowBuffer, WINDOW_SIZE);

  Serial.println("=================================");
  Serial.println();
}