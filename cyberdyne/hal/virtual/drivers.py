from __future__ import annotations

import math
import random

from ...kernel.config import WorldConfig
from ...sim.world import Twist, World
from ..interfaces import IMU, Battery, BatteryReading, DriveBase, IMUReading, Odometry, RangeSensor, ScanPoint
from ..registry import DeviceRegistry


class VirtualDrive(DriveBase):
    device_id = "drive.virtual"

    def __init__(self, world: World) -> None:
        self.world = world

    async def set_velocity(self, linear: float, angular: float) -> None:
        self.world.cmd = Twist(linear, angular)

    async def odometry(self) -> Odometry:
        p, c = self.world.robot, self.world.cmd
        return Odometry(p.x, p.y, p.theta, c.linear, c.angular)


class VirtualRangeSensor(RangeSensor):
    device_id = "range.virtual"

    def __init__(self, world: World, beams: int = 16, fov: float = math.pi,
                 max_range: float = 4.0, noise: float = 0.0, seed: int = 0) -> None:
        self.world = world
        self.beams = beams
        self.fov = fov
        self.max_range = max_range
        self.noise = noise
        self._rng = random.Random(seed)
        self.fault_injected = False

    async def scan(self) -> list[ScanPoint]:
        if self.fault_injected:
            raise OSError("range sensor fault (injected)")
        p = self.world.robot
        pts = []
        for i in range(self.beams):
            a = -self.fov / 2 + self.fov * i / (self.beams - 1) if self.beams > 1 else 0.0
            d = self.world.raycast(p.x, p.y, p.theta + a, self.max_range)
            if self.noise:
                d = max(0.0, min(self.max_range, d + self._rng.gauss(0, self.noise)))
            pts.append(ScanPoint(a, d))
        return pts

    async def self_test(self) -> tuple[bool, str]:
        return (not self.fault_injected), ("fault injected" if self.fault_injected else "ok")


class VirtualBattery(Battery):
    device_id = "battery.virtual"

    def __init__(self, world: World, cfg: WorldConfig) -> None:
        self.world = world
        self.level = cfg.battery_start
        self.drain_idle = cfg.battery_drain_idle
        self.drain_moving = cfg.battery_drain_moving
        self.charge_rate = cfg.battery_charge_rate
        self._last_t = world.time

    def integrate(self) -> None:
        dt = self.world.time - self._last_t
        self._last_t = self.world.time
        if dt <= 0:
            return
        if self.world.at_charger() and abs(self.world.cmd.linear) < 1e-6:
            self.level = min(1.0, self.level + self.charge_rate * dt)
        else:
            speed = abs(self.world.cmd.linear) + 0.3 * abs(self.world.cmd.angular)
            self.level = max(0.0, self.level - (self.drain_idle + self.drain_moving * speed) * dt)

    async def read(self) -> BatteryReading:
        self.integrate()
        charging = self.world.at_charger() and abs(self.world.cmd.linear) < 1e-6
        return BatteryReading(self.level, charging, voltage=10.5 + 2.1 * self.level)


class VirtualIMU(IMU):
    device_id = "imu.virtual"

    def __init__(self, world: World) -> None:
        self.world = world

    async def read(self) -> IMUReading:
        return IMUReading(self.world.robot.theta, self.world.cmd.angular)


def build_virtual_devices(world: World, cfg: WorldConfig, registry: DeviceRegistry) -> None:
    registry.register(VirtualDrive(world))
    registry.register(VirtualRangeSensor(world))
    registry.register(VirtualBattery(world, cfg))
    registry.register(VirtualIMU(world))
