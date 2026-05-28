#include <Arduino.h>
#include <HX711.h>

// =========================
// Arduino Mega pin mapping
// =========================
// All HX711 boards share the same SCK line.
static const uint8_t HX711_SCK_PIN = 2;

// DT pins, one per HX711/load cell
static const uint8_t HX711_DOUT_PINS[6] = {
  22, // Load cell 1
  23, // Load cell 2
  24, // Load cell 3
  25, // Load cell 4
  26, // Load cell 5
  27  // Load cell 6
};

// Your calibrated values
static float calibrationFactors[6] = {
  -426.090911f,
  355.842437f,
  838.175781f,
  334.224243f,
  1952.721191f,
  634.533325f
};

static long offsets[6] = {
  -27544,
  199694,
  283166,
  18642,
  578639,
  237369
};

HX711 scales[6];

static void printHeader() {
  Serial.println();
  Serial.println("HX711 6-load-cell live read");
  Serial.println("Commands:");
  Serial.println("  p -> print one line now");
  Serial.println("  t -> tare all cells (empty platform)");
  Serial.println();
}

static void applyCalibration() {
  for (int i = 0; i < 6; i++) {
    scales[i].set_scale(calibrationFactors[i]);
    scales[i].set_offset(offsets[i]);
  }
}

static void tareAll() {
  Serial.println("Taring all cells...");
  for (int i = 0; i < 6; i++) {
    if (scales[i].is_ready()) {
      scales[i].tare(15);
      offsets[i] = scales[i].get_offset();
    }
  }
  Serial.println("Tare complete.");
}

static void printWeights() {
  Serial.print("g: ");
  for (int i = 0; i < 6; i++) {
    Serial.print("L");
    Serial.print(i + 1);
    Serial.print("=");

    if (scales[i].is_ready()) {
      float grams = scales[i].get_units(8);
      Serial.print(grams, 1);
    } else {
      Serial.print("NA");
    }

    if (i < 5) {
      Serial.print(" | ");
    }
  }
  Serial.println();
}

void setup() {
  Serial.begin(9600);
  while (!Serial) {
    ;
  }

  for (int i = 0; i < 6; i++) {
    scales[i].begin(HX711_DOUT_PINS[i], HX711_SCK_PIN);
  }

  applyCalibration();
  printHeader();
}

void loop() {
  if (Serial.available()) {
    char c = (char)Serial.read();
    if (c == 'p' || c == 'P') {
      printWeights();
    } else if (c == 't' || c == 'T') {
      tareAll();
      printWeights();
    }
    while (Serial.available()) {
      Serial.read();
    }
  }

  printWeights();
  delay(350);
}
