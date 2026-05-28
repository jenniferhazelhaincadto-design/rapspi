#include <Arduino.h>
#include <Stepper.h>

static const int STEPS_PER_REV = 100;
static const int MOTOR_RPM = 180;

Stepper stepper7(STEPS_PER_REV, A0, A1, A2, A3);

void setup() {
  Serial.begin(9600);
  stepper7.setSpeed(MOTOR_RPM);
  Serial.println("Stepper 7 ready");
  Serial.println("Send 'f' for CW 360, 'r' for CCW 360");
}

void loop() {
  if (!Serial.available()) {
    return;
  }

  const char cmd = (char)Serial.read();
  if (cmd == 'f' || cmd == 'F') {
    stepper7.step(STEPS_PER_REV * 25);
    Serial.println("M7 CW 360 done");
  } else if (cmd == 'r' || cmd == 'R') {
    stepper7.step(-STEPS_PER_REV * 25);
    Serial.println("M7 CCW 360 done");
  }

  while (Serial.available()) {
    Serial.read();
  }
}
