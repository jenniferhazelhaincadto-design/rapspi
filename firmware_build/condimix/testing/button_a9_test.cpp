#include <Arduino.h>

static const uint8_t BUTTON_PIN = A9;
static const bool BUTTON_ACTIVE_LOW = true;
static const unsigned long DEBOUNCE_MS = 50;

static int lastRawState = HIGH;
static int stableState = HIGH;
static unsigned long lastChangeMs = 0;

void setup() {
  Serial.begin(9600);
  pinMode(BUTTON_PIN, INPUT_PULLUP);

  lastRawState = digitalRead(BUTTON_PIN);
  stableState = lastRawState;
  lastChangeMs = millis();

  Serial.println("A9 button test ready");
  Serial.println("Wiring: button to A9 and GND, using INPUT_PULLUP");
  Serial.println("LOW = pressed, HIGH = released");
}

void loop() {
  int rawState = digitalRead(BUTTON_PIN);

  if (rawState != lastRawState) {
    lastRawState = rawState;
    lastChangeMs = millis();
    Serial.print("Raw change: ");
    Serial.println(rawState == LOW ? "LOW" : "HIGH");
  }

  if ((millis() - lastChangeMs) >= DEBOUNCE_MS && rawState != stableState) {
    stableState = rawState;
    if (BUTTON_ACTIVE_LOW) {
      if (stableState == LOW) {
        Serial.println("Button PRESSED");
      } else {
        Serial.println("Button RELEASED");
      }
    } else {
      if (stableState == HIGH) {
        Serial.println("Button PRESSED");
      } else {
        Serial.println("Button RELEASED");
      }
    }
  }

  static unsigned long lastHeartbeatMs = 0;
  if (millis() - lastHeartbeatMs >= 500) {
    lastHeartbeatMs = millis();
    Serial.print("A9 raw=");
    Serial.print(rawState == LOW ? "LOW" : "HIGH");
    Serial.print(" stable=");
    Serial.println(stableState == LOW ? "LOW" : "HIGH");
  }
}
