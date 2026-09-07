"""Hardware abstraction interfaces.

Every physical capability is an abstract class here. Real drivers (Raspberry
Pi GPIO, serial, ROS bridge) and the virtual backend implement the same
interface, so nothing above the HAL knows whether it is running on silicon
or in a simulation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum


class DeviceKind(StrEnum):
    DRIVE = "drive"
    RANGE = "range"
    BATTERY = "battery"
    IMU = "imu"
    CAMERA = "camera"
    MIC = "mic"
    SPEAKER = "speaker"


class Device(ABC):
    kind: DeviceKind
    device_id: str = "dev"

    async def open(self) -> None:  # noqa: B027
        pass

    async def close(self) -> None:  # noqa: B027
        pass

    async def self_test(self) -> tuple[bool, str]:
        return True, "ok"

    def describe(self) -> dict:
        return {"kind": self.kind.value, "id": self.device_id, "driver": type(self).__name__}


@dataclass
class Odometry:
    x: float
    y: float
    theta: float
    linear: float
    angular: float

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class DriveBase(Device):
    kind = DeviceKind.DRIVE

    @abstractmethod
    async def set_velocity(self, linear: float, angular: float) -> None: ...

    @abstractmethod
    async def odometry(self) -> Odometry: ...

    async def stop(self) -> None:
        await self.set_velocity(0.0, 0.0)


@dataclass
class ScanPoint:
    angle: float      # radians, robot frame, 0 = forward
    distance: float   # metres (== max_range if nothing hit)


class RangeSensor(Device):
    kind = DeviceKind.RANGE
    max_range: float = 4.0

    @abstractmethod
    async def scan(self) -> list[ScanPoint]: ...


@dataclass
class BatteryReading:
    level: float          # 0..1
    charging: bool
    voltage: float = 0.0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class Battery(Device):
    kind = DeviceKind.BATTERY

    @abstractmethod
    async def read(self) -> BatteryReading: ...


@dataclass
class IMUReading:
    heading: float
    angular_velocity: float
    accel_x: float = 0.0
    accel_y: float = 0.0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class IMU(Device):
    kind = DeviceKind.IMU

    @abstractmethod
    async def read(self) -> IMUReading: ...


@dataclass
class Detection:
    kind: str            # "person" | "object" | ...
    label: str           # detector label ("person", "cup"...)
    bearing: float       # radians, robot frame
    distance: float      # metres
    confidence: float
    signature: str = ""  # identity embedding stand-in (face / object fingerprint)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Frame:
    ts: float
    width: int
    height: int
    detections: list[Detection]
    pixels: bytes | None = None   # real cameras carry pixels; the detector fills ``detections``


class Camera(Device):
    kind = DeviceKind.CAMERA
    fov: float = 1.2          # radians
    max_range: float = 6.0

    @abstractmethod
    async def capture(self) -> Frame: ...


@dataclass
class Utterance:
    text: str
    ts: float
    signature: str = ""       # speaker embedding stand-in
    loudness: float = 1.0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class Microphone(Device):
    kind = DeviceKind.MIC

    @abstractmethod
    async def listen(self) -> list[Utterance]:
        """Drain utterances heard since the last call."""


class Speaker(Device):
    kind = DeviceKind.SPEAKER

    @abstractmethod
    async def say(self, text: str, voice: str = "neutral") -> None: ...
