"""SensorHub: polls low-rate devices, fuses pose, publishes readings.

    sensor/odometry      {x,y,theta,linear,angular}   fused pose (EKF) when perception.fuse_pose, else raw
    sensor/odometry_raw  the drive's own dead reckoning
    sensor/pose_cov      scalar position variance of the fused estimate
    sensor/battery       {level,charging,voltage}
    sensor/imu           {heading,angular_velocity,...}

Docking on the charger is an absolute fix (the pad's position is known).
"""
from __future__ import annotations

from ..hal.interfaces import IMU, Battery, DeviceKind, DriveBase
from ..kernel.context import Context
from ..kernel.module import Module
from .fusion import PoseFilter


class SensorHub(Module):
    name = "sensors"
    rate_hz = 20.0
    priority = 10

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        self.drive = ctx.devices.get(DeviceKind.DRIVE, DriveBase)
        self.battery = ctx.devices.get(DeviceKind.BATTERY, Battery) if ctx.devices.has(DeviceKind.BATTERY) else None
        self.imu = ctx.devices.get(DeviceKind.IMU, IMU) if ctx.devices.has(DeviceKind.IMU) else None
        w = ctx.config.world
        start = w.robot_start
        self.filter = (PoseFilter(start["x"], start["y"], start.get("theta", 0.0), w.width, w.height)
                       if ctx.config.perception.fuse_pose else None)
        self.range_max = ctx.devices.get(DeviceKind.RANGE).max_range if ctx.devices.has(DeviceKind.RANGE) else 4.0
        self._docked = False

    async def tick(self, dt: float) -> None:
        bus = self.ctx.bus
        odom = await self.drive.odometry()
        raw = odom.to_dict()
        imu = await self.imu.read() if self.imu else None
        batt = await self.battery.read() if self.battery else None
        if self.filter is not None:
            f = self.filter
            f.predict(odom.x, odom.y, odom.theta)
            if imu is not None:
                f.update_heading(imu.heading)
            scan = bus.latest_payload("perception/scan")
            if scan:
                f.update_walls(scan, self.range_max)
            charging = bool(batt and batt.charging)
            if charging and not self._docked:
                c = self.ctx.config.world.charger
                f.fix(c["x"], c["y"])
            self._docked = charging
            fp = f.pose()
            fused = {"x": fp.x, "y": fp.y, "theta": fp.theta, "linear": odom.linear, "angular": odom.angular}
            await bus.publish("sensor/odometry", fused, source=self.name)
            await bus.publish("sensor/pose_cov", fp.cov, source=self.name)
        else:
            await bus.publish("sensor/odometry", raw, source=self.name)
        await bus.publish("sensor/odometry_raw", raw, source=self.name)
        if batt is not None:
            await bus.publish("sensor/battery", batt.to_dict(), source=self.name)
        if imu is not None:
            await bus.publish("sensor/imu", imu.to_dict(), source=self.name)
