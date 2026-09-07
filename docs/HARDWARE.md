# Hardware: the serial bridge

The first physical body is a **two-board** design:

* a computer running the brain (Raspberry Pi 4/5, or any laptop) running
  `cyberdyne run` in `mode = "serial"`;
* a microcontroller (Arduino, ESP32, RP2040) driving the motors, reading
  encoders, an ultrasonic/ToF array or a small lidar, and a battery monitor.

They talk over USB serial with a **line protocol**: one ASCII command per
line, one reply per command. It is trivial to implement in firmware and easy
to watch with a terminal.

```
Host -> MCU               MCU -> Host
PING                      PONG <name> <version>
VEL <lin m/s> <ang rad/s> OK VEL
ODOM?                     ODOM <x> <y> <theta> <v> <w>          (metres, radians, robot frame at boot)
SCAN?                     SCAN <angle>:<dist>,<angle>:<dist>,... (radians, metres; 0 = forward)
BATT?                     BATT <level 0..1> <volts> <charging 0|1>
<anything else>           ERR unknown <cmd>
```

The MCU should:

* apply `VEL` immediately and **stop the motors if no `VEL` arrives for
  500 ms** (the host's safety gate sends one every 20 ms; silence means the
  host died);
* integrate wheel encoders into `ODOM` itself (dead reckoning);
* wire a **physical e-stop** into the motor driver enable line. Software
  e-stop is a second layer, never the only one.

## Config

```toml
[kernel]
mode = "serial"

[hardware]
port = "/dev/ttyUSB0"        # "loopback" runs the built-in fake firmware
baud = 115200
linear_scale = 1.0           # from calibration
angular_scale = 1.0
```

`pip install pyserial` for a real port. With `port = "loopback"` the whole
stack runs against `LoopbackTransport`, a Python fake firmware with a
deliberate wheel-scale error, which is how the backend is tested.

## Calibration

```python
from cyberdyne.hal.calibration import calibrate_drive
result = await calibrate_drive(drive, clock, distance=1.0)   # drives 1 m, turns 90 degrees
# -> CalibrationResult(linear_scale=1.25, angular_scale=1.25, notes='measured 0.800 m for 1.0 m ...')
```

Put the two numbers into `[hardware]`. Calibrate on the floor the robot
will actually run on; carpet and tile differ.

## Firmware

`firmware/cyberdyne_fw/` is an Arduino sketch (Uno/Mega/ESP32 class).
`protocol.h` holds everything hardware-independent (line parsing, the
commands, differential-drive kinematics, odometry integration, the 500 ms
command watchdog, `ESTOP`) and is compiled on the host by
`firmware/test/test_protocol.cpp` (run in the test suite when `g++` exists).
`cyberdyne_fw.ino` binds it to pins:

| Function | Pins (defaults) |
|---|---|
| Motor driver (L298N / TB6612) | L: PWM 5, DIR 4; R: PWM 6, DIR 7 |
| Quadrature encoders | L: A=2 (interrupt), B=8; R: A=3 (interrupt), B=9 |
| Ultrasonic HC-SR04 x5 (left..right) | TRIG 22,24,26,28,30; ECHO 23,25,27,29,31 |
| Battery divider | A0 (`BATT_DIVIDER` = (R1+R2)/R2) |
| Charger sense | D12 |
| Physical e-stop | cuts the driver's enable line in hardware |

Set `Config` (wheel base, ticks per metre, max wheel speed, battery
volts) in `protocol.h`, flash, then run the calibration above.

## Raspberry Pi brain

```bash
git clone <repo> && cd cyberdyne.system.model
./scripts/setup_rpi.sh          # apt deps, venv, pyserial/opencv/vosk, English Vosk model, robot.toml
.venv/bin/cyberdyne check -s robot.toml
.venv/bin/cyberdyne run -s robot.toml -d   # dashboard on http://<pi>:8080
sudo cp deploy/cyberdyne.service /etc/systemd/system/ && sudo systemctl enable --now cyberdyne
```

`mode = "rpi"` = the serial body plus:

* `OpenCVCamera`: USB / Pi camera, OpenCV HOG person detector, bearing from
  box position, distance from box height. No identity signatures yet (add a
  face-embedding model to fill `Detection.signature`).
* `VoskMicrophone`: offline speech-to-text in a background thread
  (English model by default; swap in `vosk-model-small-bn-0.4` for Bangla).
* `ShellSpeaker`: `espeak-ng` text to speech.

A missing peripheral does not stop the robot: the boot diagnostic reports it
on `kernel/degraded` and the body still runs. Only drive, range and battery
failures boot into ESTOP.

## Bill of materials (reference build)

Raspberry Pi 4 (2 GB+), Arduino Mega 2560 (or ESP32), TB6612FNG motor
driver, 2x DC gear motors with encoders (e.g. 12 V, 200 RPM), 2S/3S LiPo
with a BMS and a voltage divider, 5x HC-SR04, USB webcam, USB microphone or
ReSpeaker HAT, small speaker on the Pi's audio jack, a chassis with a caster
wheel, and a mushroom e-stop wired into the motor driver's enable line.
