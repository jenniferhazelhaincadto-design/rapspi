#include <Arduino.h>
#include <Stepper.h>

static const int STEPS_PER_REV = 100;
static const int MOTOR_RPM = 180;

Stepper stepper1(STEPS_PER_REV, 22, 23, 24, 25);

void setup() {
	Serial.begin(9600);
	stepper1.setSpeed(MOTOR_RPM);
	Serial.println("Stepper 1 ready");
	Serial.println("Send 'f' for CW 360, 'r' for CCW 360");
}

void loop() {
	if (!Serial.available()) {
		return;
	}

	const char cmd = (char)Serial.read();
	if (cmd == 'f' || cmd == 'F') {
		stepper1.step(-STEPS_PER_REV * 10);
		Serial.println("M1 CW 360 done");
	} else if (cmd == 'r' || cmd == 'R') {
		stepper1.step(STEPS_PER_REV * 10);
		Serial.println("M1 CCW 360 done");
	}

	while (Serial.available()) {
		Serial.read();
	}
}