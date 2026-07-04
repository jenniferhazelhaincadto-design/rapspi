#include <ArduinoJson.h>
#include <Stepper.h>

const int stepsPerRevolution = 200;
const int num_step = 7;

const int buttonPin = 6;
const int emergencyPin = 7;

const int PUMP_COUNT = 4;
const int pumpPins[PUMP_COUNT] = {50, 51, 52, 53};

#define PUMP_ACTIVE_LOW 1
static const uint8_t PUMP_ACTIVE_LEVEL = PUMP_ACTIVE_LOW ? LOW : HIGH;
static const uint8_t PUMP_INACTIVE_LEVEL = PUMP_ACTIVE_LOW ? HIGH : LOW;

// Keep updated motor pin ordering from origin/main.
Stepper stepper[num_step] = {
  Stepper(stepsPerRevolution, 26, 27, 28, 29),
  Stepper(stepsPerRevolution, 30, 31, 32, 33),
  Stepper(stepsPerRevolution, 34, 35, 36, 37),
  Stepper(stepsPerRevolution, 38, 39, 40, 41),
  Stepper(stepsPerRevolution, 42, 43, 44, 45),
  Stepper(stepsPerRevolution, 46, 47, 48, 49),
  Stepper(stepsPerRevolution, 22, 23, 24, 25)
};

const int stepperPins[num_step][4] = {
  {26, 27, 28, 29},
  {30, 31, 32, 33},
  {34, 35, 36, 37},
  {38, 39, 40, 41},
  {42, 43, 44, 45},
  {46, 47, 48, 49},
  {22, 23, 24, 25}
};

bool stopRequested = false;
bool emergencyLatched = false;
int currentContainer = 1;

String readLine() {
  if (!Serial.available()) {
    return "";
  }
  return Serial.readStringUntil('\n');
}

bool pollStop() {
  if (!Serial.available()) {
    return false;
  }
  String line = Serial.readStringUntil('\n');
  if (line.indexOf("\"cmd\":\"stop\"") >= 0 || line.indexOf("\"cmd\": \"stop\"") >= 0) {
    stopRequested = true;
    return true;
  }
  return false;
}

void stopAllOutputs() {
  for (int i = 0; i < PUMP_COUNT; i++) {
    digitalWrite(pumpPins[i], PUMP_INACTIVE_LEVEL);
  }
  for (int i = 0; i < num_step; i++) {
    for (int j = 0; j < 4; j++) {
      digitalWrite(stepperPins[i][j], LOW);
    }
  }
}

bool emergencyCheck() {
  if (emergencyLatched) {
    return true;
  }
  if (digitalRead(emergencyPin) == LOW) {
    emergencyLatched = true;
    stopRequested = true;
    stopAllOutputs();
    Serial.println("STATUS:EMERGENCY");
    return true;
  }
  return false;
}

bool checkUserStop() {
  if (digitalRead(buttonPin) == LOW) {
    stopRequested = true;
    Serial.println("STATUS:STOPPED");
    return true;
  }
  return false;
}

void runPump(int pumpIndex, unsigned long durationMs) {
  if (pumpIndex < 0 || pumpIndex >= PUMP_COUNT || durationMs == 0) {
    return;
  }
  digitalWrite(pumpPins[pumpIndex], PUMP_ACTIVE_LEVEL);
  unsigned long startMs = millis();
  while (millis() - startMs < durationMs) {
    if (emergencyCheck() || checkUserStop() || pollStop()) {
      break;
    }
    delay(5);
  }
  digitalWrite(pumpPins[pumpIndex], PUMP_INACTIVE_LEVEL);
}

void reposition() {
  // IR integration removed: treat current position as home for protocol compatibility.
  stopAllOutputs();
  currentContainer = 1;
}

void nextContainer() {
  stopAllOutputs();
  for (int i = 0; i < 19; i++) {
    if (emergencyCheck() || checkUserStop() || pollStop()) {
      return;
    }
    stepper[6].step(-stepsPerRevolution);
  }
  currentContainer++;
  if (currentContainer > 6) {
    currentContainer = 1;
  }
}

void moveToContainer(int target) {
  if (target < 1 || target > 6) {
    return;
  }
  while (currentContainer != target) {
    nextContainer();
    if (stopRequested || emergencyLatched) {
      return;
    }
  }
}

void dispenseDry(int targetGrams, int containerId, int stepsPerGram) {
  if (targetGrams <= 0 || containerId < 1 || containerId > 6) {
    return;
  }
  if (stepsPerGram <= 0) {
    stepsPerGram = 2;
  }

  stopAllOutputs();
  long revolutions = (long)targetGrams * (long)stepsPerGram;
  for (long i = 0; i < revolutions; i++) {
    if (emergencyCheck() || checkUserStop() || pollStop()) {
      return;
    }
    stepper[containerId - 1].step(-stepsPerRevolution);
    delay(5);
  }
}

void handleDispense(JsonDocument &doc) {
  if (emergencyCheck()) {
    return;
  }
  stopRequested = false;

  JsonArray dry = doc["dry"].as<JsonArray>();
  reposition();

  for (JsonObject item : dry) {
    if (stopRequested || emergencyLatched) {
      break;
    }
    int id = item["id"] | 0;
    int grams = item["g"] | 0;
    int stepsPerGram = item["steps_per_gram"] | 2;
    moveToContainer(id);
    dispenseDry(grams, id, stepsPerGram);
  }

  if (!stopRequested && !emergencyLatched) {
    reposition();
  }

  JsonArray wet = doc["wet"].as<JsonArray>();
  for (JsonObject item : wet) {
    if (stopRequested || emergencyLatched) {
      break;
    }
    int id = item["id"] | 0;
    float ml = item["ml"] | 0.0f;
    int msPerMl = item["ms_per_ml"] | 100;
    int pumpIndex = id - 1;
    unsigned long durationMs = (unsigned long)(ml * (float)msPerMl);
    runPump(pumpIndex, durationMs);
  }

  if (stopRequested || emergencyLatched) {
    Serial.println("STATUS:STOPPED");
  } else {
    Serial.println("STATUS:OK");
  }
}

void handleClean() {
  if (emergencyCheck()) {
    return;
  }
  stopRequested = false;
  for (int i = 0; i < PUMP_COUNT; i++) {
    if (stopRequested || emergencyLatched) {
      break;
    }
    runPump(i, 2000);
  }
  if (stopRequested || emergencyLatched) {
    Serial.println("STATUS:STOPPED");
  } else {
    Serial.println("STATUS:OK");
  }
}

void handleLevels() {
  if (emergencyCheck()) {
    return;
  }
  StaticJsonDocument<512> doc;
  doc["type"] = "levels";
  JsonArray dry = doc.createNestedArray("dry");
  for (int i = 1; i <= 6; i++) {
    JsonObject item = dry.createNestedObject();
    item["id"] = i;
    item["g"] = 0;
  }
  serializeJson(doc, Serial);
  Serial.println();
}

void handleIr() {
  StaticJsonDocument<128> doc;
  doc["type"] = "ir";
  doc["raw"] = 0;
  doc["detected"] = true;
  serializeJson(doc, Serial);
  Serial.println();
}

void setup() {
  Serial.begin(9600);
  pinMode(buttonPin, INPUT_PULLUP);
  pinMode(emergencyPin, INPUT_PULLUP);

  for (int i = 0; i < PUMP_COUNT; i++) {
    pinMode(pumpPins[i], OUTPUT);
    digitalWrite(pumpPins[i], PUMP_INACTIVE_LEVEL);
  }

  for (int i = 0; i < num_step; i++) {
    stepper[i].setSpeed(60);
  }

  for (int i = 0; i < num_step; i++) {
    for (int j = 0; j < 4; j++) {
      pinMode(stepperPins[i][j], OUTPUT);
      digitalWrite(stepperPins[i][j], LOW);
    }
  }

  Serial.println("CONDIMIX v2 ready");
}

void loop() {
  if (emergencyCheck()) {
    delay(50);
    return;
  }

  String line = readLine();
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
  if (strcmp(cmd, "dispense") == 0) {
    handleDispense(doc);
  } else if (strcmp(cmd, "clean") == 0) {
    handleClean();
  } else if (strcmp(cmd, "levels") == 0) {
    handleLevels();
  } else if (strcmp(cmd, "ir") == 0) {
    handleIr();
  } else if (strcmp(cmd, "stop") == 0) {
    stopRequested = true;
    stopAllOutputs();
    Serial.println("STATUS:STOPPED");
  } else {
    Serial.println("STATUS:ERROR");
  }
}
