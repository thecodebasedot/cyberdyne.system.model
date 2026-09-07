"""Serial bridge backend: talk to a microcontroller firmware over a line protocol.

This is the first real-hardware path. A Raspberry Pi (or laptop) runs the
brain; an Arduino/ESP32 runs motors, encoders and sensors and speaks the
protocol in ``docs/HARDWARE.md``. ``LoopbackTransport`` is a fake firmware
in Python so the whole backend is testable without a board.
"""
from .drivers import SerialBattery, SerialDrive, SerialRangeSensor, build_serial_devices
from .transport import LineTransport, LoopbackTransport, SerialTransport

__all__ = ["SerialBattery", "SerialDrive", "SerialRangeSensor", "build_serial_devices",
           "LineTransport", "LoopbackTransport", "SerialTransport"]
