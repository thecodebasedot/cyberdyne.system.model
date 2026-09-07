// Host test for the firmware protocol: g++ -std=c++17 -I../cyberdyne_fw test_protocol.cpp && ./a.out
#include "../cyberdyne_fw/protocol.h"
#include <cassert>
#include <cstring>
#include <iostream>

static float dutyL = 0, dutyR = 0; static long encL = 0, encR = 0; static unsigned long now = 0;
static void setMotors(float l, float r) { dutyL = l; dutyR = r; }
static void readEnc(long* l, long* r) { *l = encL; *r = encR; }
static float readRange(int i) { return 1.0f + i; }
static float readBatt() { return 11.55f; }
static int readCharging() { return 1; }
static unsigned long ms() { return now; }

#define CHECK(cond) do { if (!(cond)) { std::cerr << "FAIL line " << __LINE__ << ": " #cond "\n"; return 1; } } while (0)

int main() {
  Hardware hw = {setMotors, readEnc, readRange, readBatt, readCharging, ms};
  Config cfg; Robot r(hw, cfg); char out[160];

  r.handle("PING", out, sizeof out);            CHECK(!strcmp(out, "PONG cyberdyne-fw 1.0"));
  r.handle("VEL 0.30 0.00", out, sizeof out);   CHECK(!strcmp(out, "OK VEL"));
  CHECK(fabs(dutyL - 0.5f) < 1e-4 && fabs(dutyR - 0.5f) < 1e-4);          // 0.3 / 0.6 max
  r.handle("VEL 0 1.0", out, sizeof out);       CHECK(dutyL < 0 && dutyR > 0);   // turning left
  r.handle("VEL 5 0", out, sizeof out);         CHECK(dutyL == 1.0f && dutyR == 1.0f);   // clamped
  r.handle("VEL x", out, sizeof out);           CHECK(!strncmp(out, "ERR", 3));
  r.handle("FLY", out, sizeof out);             CHECK(!strcmp(out, "ERR unknown FLY"));

  // straight line: 2000 ticks each wheel = 1 m
  r.handle("VEL 0.3 0", out, sizeof out); encL = encR = 2000; r.update();
  CHECK(fabs(r.x() - 1.0f) < 1e-3 && fabs(r.y()) < 1e-3 && fabs(r.theta()) < 1e-6);
  // spin: right +, left - by quarter wheelbase*pi/2 ... dth = (dr-dl)/base
  encR += (long)(0.18f * 1.5708f / 2 * 2000); encL -= (long)(0.18f * 1.5708f / 2 * 2000); r.update();
  CHECK(fabs(r.theta() - 1.5708f) < 0.01f);
  r.handle("ODOM?", out, sizeof out);
  float ox, oy, oth, ov, ow; CHECK(sscanf(out, "ODOM %f %f %f %f %f", &ox, &oy, &oth, &ov, &ow) == 5);
  CHECK(fabs(ox - 1.0f) < 1e-3 && fabs(oy) < 1e-3 && fabs(oth - 1.5708f) < 0.01f && fabs(ov - 0.3f) < 1e-3);

  r.handle("SCAN?", out, sizeof out);
  CHECK(!strncmp(out, "SCAN -1.571:1.00,-0.785:2.00,0.000:3.00,0.785:4.00,1.571:5.00", 60));
  r.handle("BATT?", out, sizeof out);           CHECK(!strcmp(out, "BATT 0.500 11.55 1"));

  // command watchdog: no VEL for > 500 ms stops the motors
  r.handle("VEL 0.3 0", out, sizeof out); now = 400; r.update(); CHECK(dutyL > 0);
  now = 1000; r.update();                       CHECK(dutyL == 0 && dutyR == 0 && r.timeouts() == 1);
  r.handle("VEL 0.3 0", out, sizeof out);  r.handle("ESTOP", out, sizeof out); CHECK(dutyL == 0 && r.v() == 0);

  LineReader lr; char line[CYB_MAX_LINE]; const char* stream = "PING\r\nVEL 1 2\n";
  int lines = 0; for (const char* p = stream; *p; ++p) if (lr.feed(*p, line)) lines++;
  CHECK(lines == 2 && !strcmp(line, "VEL 1 2"));
  std::cout << "firmware protocol: all checks passed\n";
  return 0;
}
