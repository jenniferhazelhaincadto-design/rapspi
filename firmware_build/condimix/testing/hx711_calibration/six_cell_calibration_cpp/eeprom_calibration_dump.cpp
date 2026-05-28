#include <Arduino.h>
#include <EEPROM.h>

// EEPROM addresses used by your 6 single-cell calibration sketches.
static const int CAL_ADDR[6] = {0, 4, 8, 12, 16, 20};

static void printCalibrationFromEEPROM() {
  Serial.println();
  Serial.println("=== EEPROM Calibration Dump ===");

  for (int i = 0; i < 6; i++) {
    float cal = 0.0f;
    EEPROM.get(CAL_ADDR[i], cal);

    Serial.print("Loadcell ");
    Serial.print(i + 1);
    Serial.print(" | addr ");
    Serial.print(CAL_ADDR[i]);
    Serial.print(" | calFactor = ");

    if (isnan(cal) || isinf(cal) || cal == 0.0f) {
      Serial.println("(empty or invalid)");
    } else {
      Serial.println(cal, 6);
    }
  }

  Serial.println("===============================");
  Serial.println("Send 'p' to print again.");
}

void setup() {
  Serial.begin(57600);
  delay(10);

  Serial.println("EEPROM calibration reader");
  printCalibrationFromEEPROM();
}

void loop() {
  if (Serial.available() > 0) {
    char c = Serial.read();
    if (c == 'p' || c == 'P') {
      printCalibrationFromEEPROM();
    }
  }
}
