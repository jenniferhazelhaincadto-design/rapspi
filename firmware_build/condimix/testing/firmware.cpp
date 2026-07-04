#include <Arduino.h>
#include <ArduinoJson.h>

static const uint32_t SERIAL_BAUD = 9600;
static const int DRY_COUNT = 6;

bool stopRequested = false;

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
  StaticJsonDocument<384> doc;
  doc["type"] = "levels";
  JsonArray dry = doc.createNestedArray("dry");

  for (int i = 0; i < DRY_COUNT; i++) {
    JsonObject item = dry.createNestedObject();
    item["id"] = i + 1;
    item["g"] = 0;
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

    Serial.print("DRY ");
    Serial.print(id);
    Serial.print(" target=");
    Serial.print(grams);
    Serial.print(" g current=");
    Serial.print(0);
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
  } else if (strcmp(cmd, "ir") == 0) {
    StaticJsonDocument<128> out;
    out["type"] = "ir";
    out["raw"] = 0;
    out["detected"] = true;
    serializeJson(out, Serial);
    Serial.println();
  } else if (strcmp(cmd, "stop") == 0) {
    stopRequested = true;
    Serial.println("STATUS:STOPPED");
  } else {
    Serial.println("STATUS:ERROR");
  }
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(10);
  Serial.println("CONDIMIX serial command test ready");
  Serial.println("Commands: JSON cmd=dispense|clean|levels|ir|stop");
}

void loop() {
  String line = readLine();
  if (line.length() == 0) {
    return;
  }
  handleCommandLine(line);
}
