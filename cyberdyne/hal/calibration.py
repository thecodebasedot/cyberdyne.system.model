"""Calibration routines. Each one drives the robot through a known motion
and compares commanded against measured; the result is a config value.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..kernel.clock import Clock
from .interfaces import DriveBase


@dataclass
class CalibrationResult:
    linear_scale: float
    angular_scale: float
    notes: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


async def calibrate_drive(drive: DriveBase, clock: Clock, *, distance: float = 1.0, speed: float = 0.3,
                          turn: float = math.pi / 2, turn_rate: float = 0.5, step: float = 0.05,
                          after_step=None) -> CalibrationResult:
    """Drive ``distance`` straight, then turn ``turn`` radians; measure with the
    firmware's own odometry. Returns the scale factors that would make the
    odometry agree with the commanded motion. ``after_step`` is an optional
    hook (used by tests/loopback) called after every control step.
    """
    async def run(lin: float, ang: float, duration: float) -> tuple[float, float, float]:
        o0 = await drive.odometry()
        t = 0.0
        while t < duration:
            await drive.set_velocity(lin, ang)
            await clock.sleep(step)
            t += step
            if after_step:
                after_step(step)
        await drive.stop()
        o1 = await drive.odometry()
        return math.hypot(o1.x - o0.x, o1.y - o0.y), math.atan2(math.sin(o1.theta - o0.theta),
                                                                   math.cos(o1.theta - o0.theta)), t

    d, _, _ = await run(speed, 0.0, distance / speed)
    _, a, _ = await run(0.0, turn_rate, turn / turn_rate)
    lin_scale = distance / d if d > 1e-6 else 1.0
    ang_scale = turn / a if abs(a) > 1e-6 else 1.0
    return CalibrationResult(round(lin_scale, 4), round(ang_scale, 4),
                             f"measured {d:.3f} m for {distance} m, {a:.3f} rad for {turn:.3f} rad")
