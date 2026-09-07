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

## What is still virtual

Camera, microphone and speaker drivers for real hardware are not written
yet. The interfaces (`Camera.capture -> Frame`, `Microphone.listen ->
[Utterance]`, `Speaker.say`) are fixed; an OpenCV + detector camera, a
Whisper/Vosk microphone and a TTS speaker implement them without touching
anything above the HAL. See ROADMAP Phase 3 follow-ups.
