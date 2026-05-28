#include <Arduino.h>
#include <Stepper.h>

static const int STEPS_PER_REV = 100;
static const int MOTOR_RPM = 180;

Stepper stepper3(STEPS_PER_REV, 30, 31, 32, 33);

void setup() {
  Serial.begin(9600);
  stepper3.setSpeed(MOTOR_RPM);
  Serial.println("Stepper 3 ready");
  Serial.println("Send 'f' for CW 360, 'r' for CCW 360");
}

void loop() {
  if (!Serial.available()) {
    return;
  }

  const char cmd = (char)Serial.read();
  if (cmd == 'f' || cmd == 'F') {
    stepper3.step(STEPS_PER_REV * 10);
    Serial.println("M3 CW 360 done");
  } else if (cmd == 'r' || cmd == 'R') {
    stepper3.step(-STEPS_PER_REV * 10);
    Serial.println("M3 CCW 360 done");
  }

  while (Serial.available()) {
    Serial.read();
  }
}
