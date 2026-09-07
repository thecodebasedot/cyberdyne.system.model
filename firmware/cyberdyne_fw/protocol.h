// Cyberdyne firmware: protocol + kinematics, hardware-independent.
// Compiled into the Arduino sketch AND into a host test (g++), so the
// logic the robot's brain talks to is verified without a board.
#pragma once
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#ifndef CYB_MAX_LINE
#define CYB_MAX_LINE 96
#endif
#ifndef CYB_BEAMS
#define CYB_BEAMS 5          // ultrasonic/ToF sensors, left..right
#endif

static inline float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

struct Hardware {
  // Implemented by the sketch (real pins) or the host test (fake).
  void (*setMotors)(float left, float right);      // -1..1 duty
  void (*readEncoders)(long* left, long* right);   // ticks since boot
  float (*readRange)(int beam);                    // metres
  float (*readBatteryVolts)();
  int (*readCharging)();                           // 0/1
  unsigned long (*millis)();
};

struct Config {
  float wheelBase   = 0.18f;   // metres between wheels
  float ticksPerM   = 2000.0f; // encoder ticks per metre of wheel travel
  float maxWheelMps = 0.6f;    // wheel speed at duty 1.0
  float vFull       = 12.6f;   // battery volts at 100%
  float vEmpty      = 10.5f;
  unsigned long cmdTimeoutMs = 500;
  float beamAngles[CYB_BEAMS] = {-1.5708f, -0.7854f, 0.0f, 0.7854f, 1.5708f};
};

class Robot {
 public:
  Robot(Hardware hw, Config cfg) : hw_(hw), cfg_(cfg) {}

  // Call often: integrates odometry, enforces the command watchdog.
  void update() {
    long l, r;
    hw_.readEncoders(&l, &r);
    float dl = (l - lastL_) / cfg_.ticksPerM, dr = (r - lastR_) / cfg_.ticksPerM;
    lastL_ = l; lastR_ = r;
    float d = 0.5f * (dl + dr), dth = (dr - dl) / cfg_.wheelBase;
    th_ += dth;
    x_ += d * cosf(th_); y_ += d * sinf(th_);
    unsigned long now = hw_.millis();
    if (now - lastCmdMs_ > cfg_.cmdTimeoutMs && (v_ != 0 || w_ != 0)) {
      v_ = w_ = 0; apply(); timeouts_++;
    }
  }

  // One line in, one line out. Returns the reply length.
  int handle(const char* line, char* out, int outLen) {
    if (!strncmp(line, "PING", 4)) return snprintf(out, outLen, "PONG cyberdyne-fw 1.0");
    if (!strncmp(line, "VEL ", 4)) {
      char* end; float v = strtof(line + 4, &end); float w = strtof(end, &end);
      if (end == line + 4) return snprintf(out, outLen, "ERR bad VEL");
      v_ = v; w_ = w; lastCmdMs_ = hw_.millis(); apply();
      return snprintf(out, outLen, "OK VEL");
    }
    if (!strncmp(line, "ODOM?", 5))
      return snprintf(out, outLen, "ODOM %.4f %.4f %.4f %.3f %.3f", x_, y_, th_, v_, w_);
    if (!strncmp(line, "SCAN?", 5)) {
      int n = snprintf(out, outLen, "SCAN");
      for (int i = 0; i < CYB_BEAMS; i++)
        n += snprintf(out + n, outLen - n, "%c%.3f:%.2f", i ? ',' : ' ', cfg_.beamAngles[i], hw_.readRange(i));
      return n;
    }
    if (!strncmp(line, "BATT?", 5)) {
      float vb = hw_.readBatteryVolts();
      float level = clampf((vb - cfg_.vEmpty) / (cfg_.vFull - cfg_.vEmpty), 0.0f, 1.0f);
      return snprintf(out, outLen, "BATT %.3f %.2f %d", level, vb, hw_.readCharging());
    }
    if (!strncmp(line, "ESTOP", 5)) { v_ = w_ = 0; apply(); return snprintf(out, outLen, "OK ESTOP"); }
    char cmd[16] = {0}; sscanf(line, "%15s", cmd);
    return snprintf(out, outLen, "ERR unknown %s", cmd);
  }

  float x() const { return x_; } float y() const { return y_; } float theta() const { return th_; }
  float v() const { return v_; } float w() const { return w_; }
  unsigned long timeouts() const { return timeouts_; }

 private:
  void apply() {
    float vl = v_ - w_ * cfg_.wheelBase / 2, vr = v_ + w_ * cfg_.wheelBase / 2;
    hw_.setMotors(clampf(vl / cfg_.maxWheelMps, -1.0f, 1.0f), clampf(vr / cfg_.maxWheelMps, -1.0f, 1.0f));
  }
  Hardware hw_; Config cfg_;
  float x_ = 0, y_ = 0, th_ = 0, v_ = 0, w_ = 0;
  long lastL_ = 0, lastR_ = 0;
  unsigned long lastCmdMs_ = 0, timeouts_ = 0;
};

// Line assembler for a byte stream (serial). Returns true when a full line is ready in `line`.
class LineReader {
 public:
  bool feed(char c, char* line) {
    if (c == '\n' || c == '\r') {
      if (len_ == 0) return false;
      buf_[len_] = 0; strncpy(line, buf_, CYB_MAX_LINE); len_ = 0; return true;
    }
    if (len_ < CYB_MAX_LINE - 1) buf_[len_++] = c;
    return false;
  }
 private:
  char buf_[CYB_MAX_LINE]; int len_ = 0;
};
