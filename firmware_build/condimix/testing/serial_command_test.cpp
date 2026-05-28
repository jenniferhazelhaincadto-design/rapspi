#include <Arduino.h>
#include <ArduinoJson.h>
#include <HX711_ADC.h>
#include <EEPROM.h>
#include <math.h>

static const uint32_t SERIAL_BAUD = 9600;
static const int DRY_COUNT = 6;

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
  &LoadCell_1,
  &LoadCell_2,
  &LoadCell_3,
  &LoadCell_4,
  &LoadCell_5,
  &LoadCell_6,
};

float calFactors[DRY_COUNT] = {0, 0, 0, 0, 0, 0};
bool stopRequested = false;

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
  for (JsonObjectConst item : dry) {
    int id = item["id"] | 0;
    int grams = item["g"] | 0;

    if (id < 1 || id > DRY_COUNT || grams <= 0 || stopRequested) {
      continue;
    }

    for (int i = 0; i < 3; i++) {
      loadCells[id - 1]->update();
      delay(3);
    }
    float current = loadCells[id - 1]->getData();
    int currentShown = sanitizeGrams(current);

    Serial.print("DRY ");
    Serial.print(id);
    Serial.print(" target=");
    Serial.print(grams);
    Serial.print(" g current=");
    Serial.print(currentShown);
    Serial.println(" g");

    delay(120);
  }
}

void logWetDispense(JsonArrayConst wet) {
  for (JsonObjectConst item : wet) {
    int id = item["id"] | 0;
    float ml = item["ml"] | 0.0f;

    if (id <= 0 || ml <= 0) {
      continue;
    }

    Serial.print("WET ");
    Serial.print(id);
    Serial.print(" -> ");
    Serial.print(ml, 1);
    Serial.println(" ml");

    delay(120);
    if (stopRequested) {
      break;
    }
  }
}

void handleDispense(const JsonDocument &doc) {
  stopRequested = false;

  JsonArrayConst dry = doc["dry"].as<JsonArrayConst>();
  JsonArrayConst wet = doc["wet"].as<JsonArrayConst>();

  applyDryDispense(dry);
  if (!stopRequested) {
    logWetDispense(wet);
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
  delay(500);
  Serial.println("STATUS:OK");
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

  if (strcmp(cmd, "dispense") == 0) {
    handleDispense(doc);
  } else if (strcmp(cmd, "clean") == 0) {
    handleClean();
  } else if (strcmp(cmd, "levels") == 0) {
    printLevelsJson();
  } else if (strcmp(cmd, "stop") == 0) {
    stopRequested = true;
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
  initLoadCells();
  Serial.println("CONDIMIX serial command test ready");
  Serial.println("Commands: JSON cmd=dispense|clean|levels|stop");
}

void loop() {
  String line = readLine();
  if (line.length() == 0) {
    return;
  }
  handleCommandLine(line);
}
