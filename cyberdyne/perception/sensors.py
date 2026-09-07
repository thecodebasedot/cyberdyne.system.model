"""SensorHub: polls low-rate devices and publishes raw readings.

    sensor/odometry  {x,y,theta,linear,angular}
    sensor/battery   {level,charging,voltage}
    sensor/imu       {heading,angular_velocity,...}
"""
from __future__ import annotations

from ..hal.interfaces import IMU, Battery, DeviceKind, DriveBase
from ..kernel.context import Context
from ..kernel.module import Module


class SensorHub(Module):
    name = "sensors"
    rate_hz = 20.0
    priority = 10

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        self.drive = ctx.devices.get(DeviceKind.DRIVE, DriveBase)
        self.battery = ctx.devices.get(DeviceKind.BATTERY, Battery) if ctx.devices.has(DeviceKind.BATTERY) else None
        self.imu = ctx.devices.get(DeviceKind.IMU, IMU) if ctx.devices.has(DeviceKind.IMU) else None

    async def tick(self, dt: float) -> None:
        bus = self.ctx.bus
        odom = await self.drive.odometry()
        await bus.publish("sensor/odometry", odom.to_dict(), source=self.name)
        if self.battery:
            b = await self.battery.read()
            await bus.publish("sensor/battery", b.to_dict(), source=self.name)
        if self.imu:
            i = await self.imu.read()
            await bus.publish("sensor/imu", i.to_dict(), source=self.name)
