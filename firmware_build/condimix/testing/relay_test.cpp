#include <Arduino.h>

static const uint8_t RELAY_PINS[] = {3, 4, 5, 6};
static const size_t RELAY_COUNT = sizeof(RELAY_PINS) / sizeof(RELAY_PINS[0]);
static const unsigned long ON_TIME_MS = 1000;
static const unsigned long GAP_TIME_MS = 5000;

// Set to 1 for active-low relay boards (most common), 0 for active-high.
#define RELAY_ACTIVE_LOW 1

static const uint8_t RELAY_ACTIVE_LEVEL = RELAY_ACTIVE_LOW ? LOW : HIGH;
static const uint8_t RELAY_INACTIVE_LEVEL = RELAY_ACTIVE_LOW ? HIGH : LOW;

void allRelaysOff() {
  for (size_t i = 0; i < RELAY_COUNT; i++) {
    digitalWrite(RELAY_PINS[i], RELAY_INACTIVE_LEVEL);
  }
}

void setup() {
  Serial.begin(9600);

  for (size_t i = 0; i < RELAY_COUNT; i++) {
    pinMode(RELAY_PINS[i], OUTPUT);
  }

  allRelaysOff();

  Serial.println("Relay test ready: pins 3,4,5,6");
}

void loop() {
  for (size_t i = 0; i < RELAY_COUNT; i++) {
    uint8_t pin = RELAY_PINS[i];

    allRelaysOff();

    Serial.print("Relay ON pin ");
    Serial.println(pin);
    digitalWrite(pin, RELAY_ACTIVE_LEVEL);
    delay(ON_TIME_MS);

    allRelaysOff();
    Serial.print("Relay OFF pin ");
    Serial.println(pin);
    delay(GAP_TIME_MS);
  }
}
