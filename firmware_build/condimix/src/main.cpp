#include <ArduinoJson.h>
#include <Stepper.h>
#include <HX711_ADC.h>
#include <EEPROM.h>
#include <avr/wdt.h>

const int stepsPerRevolution = 100;
const int num_step = 7;

const int IRSensor = A5;
const int buttonPin = 6;
const int emergencyPin = 7;

const int PUMP_COUNT = 4;                      
const int pumpPins[PUMP_COUNT] = {2, 3, 4, 5};

// Most relay boards used for pumps are active-low:
//   LOW  = relay ON (pump powered)
//   HIGH = relay OFF
// If your driver is active-high, set this to 0.
#define PUMP_ACTIVE_LOW 1
static const uint8_t PUMP_ACTIVE_LEVEL = PUMP_ACTIVE_LOW ? LOW : HIGH;
static const uint8_t PUMP_INACTIVE_LEVEL = PUMP_ACTIVE_LOW ? HIGH : LOW;

const int HX711_dout_1 = 47;
const int HX711_sck_1 = 46;
const int HX711_dout_2 = 50;
const int HX711_sck_2 = 48;
const int HX711_dout_3 = 53;
const int HX711_sck_3 = 52;
const int HX711_dout_4 = 45;
const int HX711_sck_4 = 44;
const int HX711_dout_5 = 51;
const int HX711_sck_5 = 49;
const int HX711_dout_6 = 43;
const int HX711_sck_6 = 42;

const int calVal_eepromAdress_1 = 0;
const int calVal_eepromAdress_2 = 0;
const int calVal_eepromAdress_3 = 0;
const int calVal_eepromAdress_4 = 0;
const int calVal_eepromAdress_5 = 0;
const int calVal_eepromAdress_6 = 0;

Stepper stepper[num_step] = {
Stepper(stepsPerRevolution, 15, 33, 23, 12),
Stepper(stepsPerRevolution, 25, 14, 32, 22),
Stepper(stepsPerRevolution, 34, 24, 17, 35),
Stepper(stepsPerRevolution, 19, 37, 27, 16),
Stepper(stepsPerRevolution, 29, 18, 36, 26),
Stepper(stepsPerRevolution, 38, 28, 21, 39),
Stepper(stepsPerRevolution, 30, 20, 13, 31)
};

const int stepperPins[num_step][4] = {
{15, 33, 23, 12},
{25, 14, 32, 22},
{34, 24, 17, 35},
{19, 37, 27, 16},
{29, 18, 36, 26},
{38, 28, 21, 39},
{30, 20, 13, 31}
};

HX711_ADC LoadCell_1(HX711_dout_1, HX711_sck_1);
HX711_ADC LoadCell_2(HX711_dout_2, HX711_sck_2);
HX711_ADC LoadCell_3(HX711_dout_3, HX711_sck_3);
HX711_ADC LoadCell_4(HX711_dout_4, HX711_sck_4);
HX711_ADC LoadCell_5(HX711_dout_5, HX711_sck_5);
HX711_ADC LoadCell_6(HX711_dout_6, HX711_sck_6);

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

bool updateLoadCells() {
bool newData = false;
if (LoadCell_1.update()) newData = true;
LoadCell_2.update();
LoadCell_3.update();
LoadCell_4.update();
LoadCell_5.update();
LoadCell_6.update();
return newData;
}

float getLoadCellData(int index) {
if (index == 1) return LoadCell_1.getData();
if (index == 2) return LoadCell_2.getData();
if (index == 3) return LoadCell_3.getData();
if (index == 4) return LoadCell_4.getData();
if (index == 5) return LoadCell_5.getData();
if (index == 6) return LoadCell_6.getData();
return 0.0f;
}

void tareLoadCell(int index) {
if (index == 1) LoadCell_1.tareNoDelay();
if (index == 2) LoadCell_2.tareNoDelay();
if (index == 3) LoadCell_3.tareNoDelay();
if (index == 4) LoadCell_4.tareNoDelay();
if (index == 5) LoadCell_5.tareNoDelay();
if (index == 6) LoadCell_6.tareNoDelay();

bool done = false;
while (!done) {
updateLoadCells();
if (index == 1) done = LoadCell_1.getTareStatus();
if (index == 2) done = LoadCell_2.getTareStatus();
if (index == 3) done = LoadCell_3.getTareStatus();
if (index == 4) done = LoadCell_4.getTareStatus();
if (index == 5) done = LoadCell_5.getTareStatus();
if (index == 6) done = LoadCell_6.getTareStatus();
delay(5);
}
}

void reposition() {
stopAllOutputs();
while (digitalRead(IRSensor) == HIGH) {
if (emergencyCheck() || checkUserStop() || pollStop()) {
return;
}
stepper[6].step(stepsPerRevolution);
}
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
for (int i = 0; i < 5; i++) {
updateLoadCells();
delay(5);
}
StaticJsonDocument<512> doc;
doc["type"] = "levels";
JsonArray dry = doc.createNestedArray("dry");
for (int i = 1; i <= 6; i++) {
JsonObject item = dry.createNestedObject();
item["id"] = i;
item["g"] = (int)getLoadCellData(i);
}
serializeJson(doc, Serial);
Serial.println();
}

void handleIr() {
StaticJsonDocument<128> doc;
doc["type"] = "ir";
int raw = digitalRead(IRSensor);
doc["raw"] = raw;
doc["detected"] = (raw == LOW);
serializeJson(doc, Serial);
Serial.println();
}

void setup() {
Serial.begin(9600);
pinMode(IRSensor, INPUT);
pinMode(buttonPin, INPUT_PULLUP);
pinMode(emergencyPin, INPUT_PULLUP);

for (int i = 0; i < PUMP_COUNT; i++) {
pinMode(pumpPins[i], OUTPUT);
digitalWrite(pumpPins[i], PUMP_INACTIVE_LEVEL);
}

for (int i = 0; i < num_step; i++) {
stepper[i].setSpeed(600);
}
stepper[6].setSpeed(650);
for (int i = 0; i < num_step; i++) {
for (int j = 0; j < 4; j++) {
pinMode(stepperPins[i][j], OUTPUT);
}
}

float calibrationValue_1 = 696.0;
float calibrationValue_2 = 696.0;
float calibrationValue_3 = 696.0;
float calibrationValue_4 = 733.0;
float calibrationValue_5 = 696.0;
float calibrationValue_6 = 733.0;

EEPROM.get(calVal_eepromAdress_1, calibrationValue_1);
EEPROM.get(calVal_eepromAdress_2, calibrationValue_2);
EEPROM.get(calVal_eepromAdress_3, calibrationValue_3);
EEPROM.get(calVal_eepromAdress_4, calibrationValue_4);
EEPROM.get(calVal_eepromAdress_5, calibrationValue_5);
EEPROM.get(calVal_eepromAdress_6, calibrationValue_6);

LoadCell_1.begin();
LoadCell_2.begin();
LoadCell_3.begin();
LoadCell_4.begin();
LoadCell_5.begin();
LoadCell_6.begin();

unsigned long stabilizingtime = 2000;
boolean _tare = true;
byte loadcell_1_rdy = 0;
byte loadcell_2_rdy = 0;
byte loadcell_3_rdy = 0;
byte loadcell_4_rdy = 0;
byte loadcell_5_rdy = 0;
byte loadcell_6_rdy = 0;
while ((loadcell_1_rdy + loadcell_2_rdy + loadcell_3_rdy + loadcell_4_rdy + loadcell_5_rdy + loadcell_6_rdy) < 6) {
if (!loadcell_1_rdy) loadcell_1_rdy += LoadCell_1.startMultiple(stabilizingtime, _tare);
if (!loadcell_2_rdy) loadcell_2_rdy += LoadCell_2.startMultiple(stabilizingtime, _tare);
if (!loadcell_3_rdy) loadcell_3_rdy += LoadCell_3.startMultiple(stabilizingtime, _tare);
if (!loadcell_4_rdy) loadcell_4_rdy += LoadCell_4.startMultiple(stabilizingtime, _tare);
if (!loadcell_5_rdy) loadcell_5_rdy += LoadCell_5.startMultiple(stabilizingtime, _tare);
if (!loadcell_6_rdy) loadcell_6_rdy += LoadCell_6.startMultiple(stabilizingtime, _tare);
}

LoadCell_1.setCalFactor(calibrationValue_1);
LoadCell_2.setCalFactor(calibrationValue_2);
LoadCell_3.setCalFactor(calibrationValue_3);
LoadCell_4.setCalFactor(calibrationValue_4);
LoadCell_5.setCalFactor(calibrationValue_5);
LoadCell_6.setCalFactor(calibrationValue_6);

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
