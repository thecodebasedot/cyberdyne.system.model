// Cyberdyne firmware for Arduino-class boards (tested logic: firmware/test).
// Wiring (defaults, change below): L298N/TB6612 motor driver, two quadrature
// encoders on interrupt pins, up to 5 HC-SR04 ultrasonic sensors, battery
// divider on A0, charger sense on D12, physical e-stop cuts the driver's
// enable line in hardware (not through this code).
#include "protocol.h"

const int PIN_L_PWM = 5, PIN_L_DIR = 4, PIN_R_PWM = 6, PIN_R_DIR = 7;
const int PIN_L_ENC_A = 2, PIN_L_ENC_B = 8, PIN_R_ENC_A = 3, PIN_R_ENC_B = 9;
const int PIN_TRIG[CYB_BEAMS] = {22, 24, 26, 28, 30}, PIN_ECHO[CYB_BEAMS] = {23, 25, 27, 29, 31};
const int PIN_BATT = A0, PIN_CHARGING = 12;
const float BATT_DIVIDER = 3.0f;      // (R1+R2)/R2 of the voltage divider

volatile long encL = 0, encR = 0;
void isrL() { encL += digitalRead(PIN_L_ENC_B) ? 1 : -1; }
void isrR() { encR += digitalRead(PIN_R_ENC_B) ? -1 : 1; }

void hwSetMotors(float l, float r) {
  digitalWrite(PIN_L_DIR, l >= 0); analogWrite(PIN_L_PWM, (int)(fabs(l) * 255));
  digitalWrite(PIN_R_DIR, r >= 0); analogWrite(PIN_R_PWM, (int)(fabs(r) * 255));
}
void hwReadEncoders(long* l, long* r) { noInterrupts(); *l = encL; *r = encR; interrupts(); }
float hwReadRange(int i) {
  digitalWrite(PIN_TRIG[i], LOW); delayMicroseconds(2);
  digitalWrite(PIN_TRIG[i], HIGH); delayMicroseconds(10); digitalWrite(PIN_TRIG[i], LOW);
  unsigned long us = pulseIn(PIN_ECHO[i], HIGH, 25000UL);   // ~4 m max
  return us ? us / 5800.0f : 4.0f;
}
float hwReadBatteryVolts() { return analogRead(PIN_BATT) * (5.0f / 1023.0f) * BATT_DIVIDER; }
int hwReadCharging() { return digitalRead(PIN_CHARGING); }
unsigned long hwMillis() { return millis(); }

Hardware hw = {hwSetMotors, hwReadEncoders, hwReadRange, hwReadBatteryVolts, hwReadCharging, hwMillis};
Config cfg;
Robot robot(hw, cfg);
LineReader reader;
char line[CYB_MAX_LINE], out[160];

void setup() {
  Serial.begin(115200);
  pinMode(PIN_L_PWM, OUTPUT); pinMode(PIN_L_DIR, OUTPUT); pinMode(PIN_R_PWM, OUTPUT); pinMode(PIN_R_DIR, OUTPUT);
  pinMode(PIN_L_ENC_A, INPUT_PULLUP); pinMode(PIN_L_ENC_B, INPUT_PULLUP);
  pinMode(PIN_R_ENC_A, INPUT_PULLUP); pinMode(PIN_R_ENC_B, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_L_ENC_A), isrL, RISING);
  attachInterrupt(digitalPinToInterrupt(PIN_R_ENC_A), isrR, RISING);
  for (int i = 0; i < CYB_BEAMS; i++) { pinMode(PIN_TRIG[i], OUTPUT); pinMode(PIN_ECHO[i], INPUT); }
  pinMode(PIN_CHARGING, INPUT);
  hwSetMotors(0, 0);
}

void loop() {
  robot.update();                          // odometry + 500 ms command watchdog
  while (Serial.available()) {
    if (reader.feed((char)Serial.read(), line)) {
      robot.handle(line, out, sizeof out);
      Serial.println(out);
    }
  }
}
