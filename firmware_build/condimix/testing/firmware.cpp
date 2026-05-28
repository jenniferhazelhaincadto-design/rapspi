#include <Arduino.h>
#include <ArduinoJson.h>
#include <HX711_ADC.h>
#include <EEPROM.h>
#include <math.h>
#include <Stepper.h>

static const uint32_t SERIAL_BAUD = 9600;

// --- Stepper Motor Configuration (Dry) ---
static const int DRY_COUNT = 6;
static const int STEPS_PER_REV = 100;
static const int MOTOR_RPM = 180;

Stepper stepper1(STEPS_PER_REV, 22, 23, 24, 25);
Stepper stepper2(STEPS_PER_REV, 26, 27, 28, 29);
Stepper stepper3(STEPS_PER_REV, 30, 31, 32, 33);
Stepper stepper4(STEPS_PER_REV, 34, 35, 36, 37);
Stepper stepper5(STEPS_PER_REV, 38, 39, 40, 41);
Stepper stepper6(STEPS_PER_REV, 7, 8, 9, 10);
Stepper stepper7(STEPS_PER_REV, A0, A1, A2, A3);

Stepper* dispensers[DRY_COUNT] = {
  &stepper1, &stepper2, &stepper3,
  &stepper4, &stepper5, &stepper6
};

// --- Emergency Stop Button ---
static const uint8_t EMERGENCY_STOP_PIN = A9;
static const unsigned long EMERGENCY_STOP_HOLD_MS = 5000;

// --- Relay Configuration (Wet) ---
static const uint8_t RELAY_PINS[] = {3, 4, 5, 6};
static const size_t RELAY_COUNT = sizeof(RELAY_PINS) / sizeof(RELAY_PINS[0]);

// Set to 1 for active-low relay boards (most common), 0 for active-high.
#define RELAY_ACTIVE_LOW 1
static const uint8_t RELAY_ACTIVE_LEVEL = RELAY_ACTIVE_LOW ? LOW : HIGH;
static const uint8_t RELAY_INACTIVE_LEVEL = RELAY_ACTIVE_LOW ? HIGH : LOW;

// --- Load Cell Configuration ---
static const int HX711_DOUT_PINS[DRY_COUNT] = {47, 50, 53, 45, 51, 43};
static const int HX711_SCK_PINS[DRY_COUNT] = {46, 48, 52, 44, 49, 42};
static const int CAL_EEPROM_ADDR[DRY_COUNT] = {0, 4, 8, 12, 16, 20};
static const float DEFAULT_CAL[DRY_COUNT] = {
  -426.090911f,
  355.842437f,
  838.175781f,
  334.224243f,
  1952.721191f,
  634.533325f,
};

HX711_ADC LoadCell_1(HX711_DOUT_PINS[0], HX711_SCK_PINS[0]);
HX711_ADC LoadCell_2(HX711_DOUT_PINS[1], HX711_SCK_PINS[1]);
HX711_ADC LoadCell_3(HX711_DOUT_PINS[2], HX711_SCK_PINS[2]);
HX711_ADC LoadCell_4(HX711_DOUT_PINS[3], HX711_SCK_PINS[3]);
HX711_ADC LoadCell_5(HX711_DOUT_PINS[4], HX711_SCK_PINS[4]);
HX711_ADC LoadCell_6(HX711_DOUT_PINS[5], HX711_SCK_PINS[5]);

HX711_ADC* loadCells[DRY_COUNT] = {
  &LoadCell_1, &LoadCell_2, &LoadCell_3,
  &LoadCell_4, &LoadCell_5, &LoadCell_6,
};

float calFactors[DRY_COUNT] = {0, 0, 0, 0, 0, 0};
bool stopRequested = false;
unsigned long emergencyStopPressedSinceMs = 0;


// --- Helper Functions ---

void allRelaysOff() {
  for (size_t i = 0; i < RELAY_COUNT; i++) {
    digitalWrite(RELAY_PINS[i], RELAY_INACTIVE_LEVEL);
  }
}

bool updateEmergencyStopButton() {
  bool pressed = (digitalRead(EMERGENCY_STOP_PIN) == LOW);

  if (!pressed) {
    emergencyStopPressedSinceMs = 0;
    return false;
  }

  unsigned long nowMs = millis();
  if (emergencyStopPressedSinceMs == 0) {
    emergencyStopPressedSinceMs = nowMs;
    return false;
  }

  if ((nowMs - emergencyStopPressedSinceMs) < EMERGENCY_STOP_HOLD_MS) {
    return false;
  }

  if (!stopRequested) {
    stopRequested = true;
    allRelaysOff();
    Serial.println("EMERGENCY STOP BUTTON PRESSED");
  }
  return true;
}

bool delayWithEmergencyStop(unsigned long durationMs) {
  unsigned long startMs = millis();
  while (!stopRequested && (millis() - startMs < durationMs)) {
    if (updateEmergencyStopButton()) {
      return false;
    }
    delay(5);
  }
  return !stopRequested;
}

bool stepWithEmergencyStop(Stepper *motor, int steps) {
  int direction = (steps >= 0) ? 1 : -1;
  int remaining = abs(steps);
  const int chunkSteps = 4;

  while (remaining > 0 && !stopRequested) {
    if (updateEmergencyStopButton()) {
      return false;
    }

    int take = remaining > chunkSteps ? chunkSteps : remaining;
    motor->step(direction * take);
    remaining -= take;
  }

  return !stopRequested;
}

int sanitizeGrams(float grams) {
  if (grams <= 20.0f) {
    return 0;
  }
  return (int)roundf(grams);
}

String readLine() {
  static String buffer;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') {
      continue;
    }
    if (c == '\n') {
      String line = buffer;
      buffer = "";
      line.trim();
      return line;
    }
    buffer += c;
    if (buffer.length() > 1024) {
      buffer = "";
    }
  }
  return "";
}

// --- Component Handlers ---

void printLevelsJson() {
  for (int i = 0; i < 5; i++) {
    for (int j = 0; j < DRY_COUNT; j++) {
      loadCells[j]->update();
    }
    delay(5);
  }

  StaticJsonDocument<384> doc;
  doc["type"] = "levels";
  JsonArray dry = doc.createNestedArray("dry");

  for (int i = 0; i < DRY_COUNT; i++) {
    JsonObject item = dry.createNestedObject();
    item["id"] = i + 1;
    item["g"] = sanitizeGrams(loadCells[i]->getData());
  }

  serializeJson(doc, Serial);
  Serial.println();
}

void applyDryDispense(JsonArrayConst dry) {
  int targets[DRY_COUNT] = {0};
  int maxContainerIndex = -1;

  // Pre-calculate dispensing targets and find the furthest container we need to visit
  for (JsonObjectConst item : dry) {
    int id = item["id"] | 0;
    int grams = item["g"] | 0;

    if (id >= 1 && id <= DRY_COUNT && grams > 0) {
      targets[id - 1] += grams;
      if ((id - 1) > maxContainerIndex) {
        maxContainerIndex = id - 1; // Keep track of the furthest container
      }
    }
  }

  // If there's nothing to dispense, just exit
  if (maxContainerIndex == -1) return;

  int absolutePositions[DRY_COUNT] = {0, 25, 49, 72, 94, 115};
  int currentCatcherPos = 0;

  // Workflow: Jump directly to containers that have > 0 values
  for (int i = 0; i <= maxContainerIndex; i++) {
    if (stopRequested) break;

    // Completely skip this container if there is no value
    if (targets[i] == 0) {
      continue; 
    }

    // 1. Navigate Stepper 7 to the active container
    int targetPos = absolutePositions[i];
    int moveAmount = targetPos - currentCatcherPos; // Calculates the jump distance

    if (moveAmount > 0) {
      Serial.print("Stepper 7 navigating to Container ");
      Serial.println(i + 1);
      
      if (!stepWithEmergencyStop(&stepper7, -STEPS_PER_REV * moveAmount)) {
        break;
      }
      if (!delayWithEmergencyStop(3000)) {
        break;
      }
      
      currentCatcherPos = targetPos; // Update where the catcher currently is
    }

    // 2. Dispense
    Serial.print("Dispensing ");
    Serial.print(targets[i]);
    Serial.print("g from Container ");
    Serial.println(i + 1);

    // Get steps_per_gram multiplier from payload, default to 2
    int stepsPerGram = 2;
    for (JsonObjectConst item : dry) {
      if ((item["id"] | 0) == i + 1) {
        stepsPerGram = item["steps_per_gram"] | 2;
        break;
      }
    }

    int dispenseMultiplier = targets[i] * stepsPerGram;
    
    // Reversed wiring logic check for Stepper 1 (index 0)
    if (i == 0) {
      if (!stepWithEmergencyStop(dispensers[i], STEPS_PER_REV * dispenseMultiplier)) {
        break;
      }
    } else {
      if (!stepWithEmergencyStop(dispensers[i], -STEPS_PER_REV * dispenseMultiplier)) {
        break;
      }
    }

    if (!delayWithEmergencyStop(3000)) {
      break;
    }
  }

  // Workflow: Stepper 7 returns to its original home position (Container 1)
  if (currentCatcherPos > 0 && !stopRequested) {
    Serial.println("Stepper 7 returning to home position");
    stepWithEmergencyStop(&stepper7, STEPS_PER_REV * currentCatcherPos); // Uses positive value to return
    delayWithEmergencyStop(3000);
  }
}

void applyWetDispense(JsonArrayConst wet) {
  for (JsonObjectConst item : wet) {
    if (stopRequested) break;

    int id = item["id"] | 0;
    float ml = item["ml"] | 0.0f;
    // Default fallback changed to 1000 (1ml = 1sec) to match physical reality
    int ms_per_ml = item["ms_per_ml"] | 1000; 

    // Map ID (1..4) to RELAY_COUNT array bounds
    if (id < 1 || id > (int)RELAY_COUNT || ml <= 0) {
      continue;
    }

    unsigned long durationMs = (unsigned long)(ml * ms_per_ml);
    uint8_t targetPin = RELAY_PINS[id - 1];

    Serial.print("WET ");
    Serial.print(id);
    Serial.print(" -> ");
    Serial.print(ml, 1);
    Serial.print(" ml (Relay Pin ");
    Serial.print(targetPin);
    Serial.print(", Time: ");
    Serial.print(durationMs);
    Serial.println("ms)");

    // Turn ON
    digitalWrite(targetPin, RELAY_ACTIVE_LEVEL);
    if (!delayWithEmergencyStop(durationMs)) {
      digitalWrite(targetPin, RELAY_INACTIVE_LEVEL);
      break;
    }
    
    // Turn OFF
    digitalWrite(targetPin, RELAY_INACTIVE_LEVEL);
    if (!delayWithEmergencyStop(500)) {
      break;
    }
  }
}

void handleDispense(const JsonDocument &doc) {
  stopRequested = false;

  JsonArrayConst dry = doc["dry"].as<JsonArrayConst>();
  JsonArrayConst wet = doc["wet"].as<JsonArrayConst>();
  int batches = doc["batches"] | 1;
  if (batches < 1) {
    batches = 1;
  }

  // Backward-compatible mode:
  // - per_batch=false (default): payload already contains total dry/wet amounts.
  // - per_batch=true: run the same dry->wet sequence once per batch.
  bool perBatch = doc["per_batch"] | false;

  const char *recipeName = doc["recipe"] | "single";
  Serial.print("DISPENSE recipe=");
  Serial.print(recipeName);
  Serial.print(" batches=");
  Serial.print(batches);
  Serial.print(" per_batch=");
  Serial.println(perBatch ? "true" : "false");

  if (perBatch) {
    for (int b = 0; b < batches; b++) {
      if (stopRequested) break;

      Serial.print("BATCH ");
      Serial.print(b + 1);
      Serial.print("/");
      Serial.println(batches);

      // Dry operations always happen first for each batch
      applyDryDispense(dry);

      // Wet operations always happen after dry
      if (!stopRequested) {
        applyWetDispense(wet);
      }
    }
  } else {
    // Dry operations always happen first
    applyDryDispense(dry);

    // Wet operations trigger ONLY AFTER all dry are completed
    if (!stopRequested) {
      applyWetDispense(wet);
    }
  }

  if (stopRequested) {
    Serial.println("STATUS:STOPPED");
  } else {
    Serial.println("STATUS:OK");
  }
}

void handleClean() {
  stopRequested = false;
  Serial.println("CLEAN:start");

  // Clean dry containers only: run steppers 1..6 one-by-one, 10 seconds each.
  allRelaysOff();

  for (int i = 0; i < DRY_COUNT; i++) {
    if (stopRequested) {
      break;
    }

    Serial.print("CLEAN: dry container ");
    Serial.print(i + 1);
    Serial.println(" for 10s");

    int direction = (i == 0) ? 1 : -1;
    if (!stepWithEmergencyStop(dispensers[i], direction * STEPS_PER_REV * 10)) {
      break;
    }
    if (!delayWithEmergencyStop(10000)) {
      break;
    }
    if (!delayWithEmergencyStop(300)) {
      break;
    }
  }

  if (stopRequested) {
    Serial.println("STATUS:STOPPED");
  } else {
    Serial.println("STATUS:OK");
  }
}

void handleCommandLine(const String &line) {
  if (line.length() == 0) {
    return;
  }

  DynamicJsonDocument doc(4096);
  DeserializationError err = deserializeJson(doc, line);
  if (err) {
    Serial.println("STATUS:ERROR");
    return;
  }

  const char *cmd = doc["cmd"] | "";

  if (strcmp(cmd, "dispense") == 0 || strcmp(cmd, "recipe") == 0) {
    handleDispense(doc);
  } else if (strcmp(cmd, "clean") == 0) {
    handleClean();
  } else if (strcmp(cmd, "levels") == 0) {
    printLevelsJson();
  } else if (strcmp(cmd, "stop") == 0) {
    stopRequested = true;
    allRelaysOff(); // Immediate safety shutoff for relays
    Serial.println("STATUS:STOPPED");
  } else {
    Serial.println("STATUS:ERROR");
  }
}

void initLoadCells() {
  for (int i = 0; i < DRY_COUNT; i++) {
    loadCells[i]->begin();
  }

  const unsigned long stabilizingTime = 2000;
  const bool tareAtStartup = true;
  byte ready[DRY_COUNT] = {0, 0, 0, 0, 0, 0};
  unsigned long startMs = millis();

  while (true) {
    int sumReady = 0;
    for (int i = 0; i < DRY_COUNT; i++) {
      if (!ready[i]) {
        ready[i] += loadCells[i]->startMultiple(stabilizingTime, tareAtStartup);
      }
      sumReady += ready[i] ? 1 : 0;
    }
    if (sumReady >= DRY_COUNT) {
      break;
    }
    if (millis() - startMs > 8000) {
      Serial.println("WARN: HX711 startup timeout");
      break;
    }
    delay(5);
  }

  for (int i = 0; i < DRY_COUNT; i++) {
    float cal = DEFAULT_CAL[i];
    EEPROM.get(CAL_EEPROM_ADDR[i], cal);
    if (isnan(cal) || isinf(cal) || cal == 0.0f) {
      cal = DEFAULT_CAL[i];
    }
    calFactors[i] = cal;
    loadCells[i]->setCalFactor(cal);

    Serial.print("LC");
    Serial.print(i + 1);
    Serial.print(" cal=");
    Serial.println(calFactors[i], 6);
  }
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(10);

  // Initialize Emergency Stop button (active low, internal pull-up)
  pinMode(EMERGENCY_STOP_PIN, INPUT_PULLUP);

  // Initialize Relays First
  for (size_t i = 0; i < RELAY_COUNT; i++) {
    pinMode(RELAY_PINS[i], OUTPUT);
  }
  allRelaysOff(); // Ensure they are definitively off immediately

  // Initialize Stepper Speeds
  stepper1.setSpeed(MOTOR_RPM);
  stepper2.setSpeed(MOTOR_RPM);
  stepper3.setSpeed(MOTOR_RPM);
  stepper4.setSpeed(MOTOR_RPM);
  stepper5.setSpeed(MOTOR_RPM);
  stepper6.setSpeed(MOTOR_RPM);
  stepper7.setSpeed(MOTOR_RPM);

  initLoadCells();
  
  Serial.println("CONDIMIX serial command test ready");
  Serial.println("Commands: JSON cmd=dispense|clean|levels|stop");
}

void loop() {
  updateEmergencyStopButton();

  String line = readLine();
  if (line.length() == 0) {
    return;
  }
  handleCommandLine(line);
}