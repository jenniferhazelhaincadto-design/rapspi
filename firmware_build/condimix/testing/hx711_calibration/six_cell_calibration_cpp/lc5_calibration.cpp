#include <Arduino.h>
#include <HX711_ADC.h>
#include <EEPROM.h>

static const int HX711_dout = 51;
static const int HX711_sck = 49;
static const int calVal_eepromAdress = 16;

HX711_ADC LoadCell(HX711_dout, HX711_sck);

void calibrate();
void changeSavedCalFactor();

void setup() {
  Serial.begin(57600);
  delay(10);
  Serial.println("Starting LC5 calibration...");

  LoadCell.begin();
  LoadCell.start(2000, true);

  if (LoadCell.getTareTimeoutFlag() || LoadCell.getSignalTimeoutFlag()) {
    Serial.println("Timeout, check MCU>HX711 wiring and pin designations");
    while (1) {}
  }

  float cal = 1.0f;
  EEPROM.get(calVal_eepromAdress, cal);
  if (cal == 0.0f || isnan(cal) || isinf(cal)) cal = 1.0f;
  LoadCell.setCalFactor(cal);

  while (!LoadCell.update()) {}
  calibrate();
}

void loop() {
  static bool newDataReady = false;

  if (LoadCell.update()) newDataReady = true;

  if (newDataReady) {
    Serial.print("Load_cell output val: ");
    Serial.println(LoadCell.getData());
    newDataReady = false;
  }

  if (Serial.available() > 0) {
    char inByte = Serial.read();
    if (inByte == 't') LoadCell.tareNoDelay();
    else if (inByte == 'r') calibrate();
    else if (inByte == 'c') changeSavedCalFactor();
  }

  if (LoadCell.getTareStatus()) Serial.println("Tare complete");
}

void calibrate() {
  Serial.println("***");
  Serial.println("Start calibration:");
  Serial.println("Remove all load then send 't'.");

  bool resume = false;
  while (!resume) {
    LoadCell.update();
    if (Serial.available() && Serial.read() == 't') LoadCell.tareNoDelay();
    if (LoadCell.getTareStatus()) resume = true;
  }

  Serial.println("Place known mass and send value in grams (example 100.0)");

  float known_mass = 0.0f;
  while (known_mass == 0.0f) {
    LoadCell.update();
    if (Serial.available()) known_mass = Serial.parseFloat();
  }

  LoadCell.refreshDataSet();
  float newCal = LoadCell.getNewCalibration(known_mass);

  Serial.print("New calibration value: ");
  Serial.println(newCal, 6);
  Serial.print("Save to EEPROM address ");
  Serial.print(calVal_eepromAdress);
  Serial.println("? y/n");

  bool done = false;
  while (!done) {
    if (Serial.available()) {
      char c = Serial.read();
      if (c == 'y') {
        EEPROM.put(calVal_eepromAdress, newCal);
        Serial.println("Value saved");
        done = true;
      } else if (c == 'n') {
        Serial.println("Value not saved");
        done = true;
      }
    }
  }

  Serial.println("***");
}

void changeSavedCalFactor() {
  Serial.println("Send new calibration value, example 696.0");

  float newCal = 0.0f;
  while (newCal == 0.0f) {
    if (Serial.available()) newCal = Serial.parseFloat();
  }

  LoadCell.setCalFactor(newCal);
  Serial.println("Save this value? y/n");

  bool done = false;
  while (!done) {
    if (Serial.available()) {
      char c = Serial.read();
      if (c == 'y') {
        EEPROM.put(calVal_eepromAdress, newCal);
        Serial.println("Value saved");
        done = true;
      } else if (c == 'n') {
        Serial.println("Value not saved");
        done = true;
      }
    }
  }
}
