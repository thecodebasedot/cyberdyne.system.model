"""PoseFilter: fuse dead-reckoned odometry with an IMU heading and wall
landmarks (the known world bounds) into a corrected pose.

Odometry drifts (wheel slip); the IMU heading drifts slowly but is absolute
over short windows; range beams that hit the outer walls are absolute
position constraints. The filter is an error-state Kalman filter on
(x, y, theta) with a scan-to-wall residual step: cheap, and it keeps a
robot honest for hours instead of minutes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class FusedPose:
    x: float
    y: float
    theta: float
    cov: float                       # scalar position variance (m^2), for the dashboard

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "theta": self.theta, "cov": round(self.cov, 4)}


def _wrap(a: float) -> float:
    return math.atan2(math.sin(a), math.cos(a))


class PoseFilter:
    def __init__(self, x: float, y: float, theta: float, width: float, height: float, *,
                 q_pos: float = 0.02, q_theta: float = 0.01, r_imu: float = 0.05, r_wall: float = 0.15,
                 wall_gain: float = 0.3) -> None:
        self.x, self.y, self.theta = x, y, theta
        self.width, self.height = width, height
        self.p_pos, self.p_theta = 0.01, 0.01
        self.q_pos, self.q_theta, self.r_imu, self.r_wall, self.wall_gain = q_pos, q_theta, r_imu, r_wall, wall_gain
        self._last_odom: tuple[float, float, float] | None = None
        self.updates = 0
        self.wall_fixes = 0

    def predict(self, odom_x: float, odom_y: float, odom_theta: float) -> None:
        """Apply the odometry *delta* in the robot frame (drift-prone, but locally accurate)."""
        if self._last_odom is None:
            self._last_odom = (odom_x, odom_y, odom_theta)
            return
        lx, ly, lth = self._last_odom
        dx, dy, dth = odom_x - lx, odom_y - ly, _wrap(odom_theta - lth)
        self._last_odom = (odom_x, odom_y, odom_theta)
        d = math.hypot(dx, dy)
        forward = d if (dx * math.cos(lth) + dy * math.sin(lth)) >= 0 else -d
        self.theta = _wrap(self.theta + dth)
        self.x += forward * math.cos(self.theta)
        self.y += forward * math.sin(self.theta)
        self.p_pos += self.q_pos * abs(forward) + 1e-5
        self.p_theta += self.q_theta * abs(dth) + 1e-5

    def update_heading(self, heading: float) -> None:
        k = self.p_theta / (self.p_theta + self.r_imu)
        self.theta = _wrap(self.theta + k * _wrap(heading - self.theta))
        self.p_theta *= (1 - k)
        self.updates += 1

    def update_walls(self, scan: list[dict], max_range: float) -> None:
        """Beams that hit an outer wall pull the position so their endpoints lie on that wall."""
        sx = sy = 0.0
        n = 0
        for p in scan:
            d = p["distance"]
            if d >= max_range - 1e-6 or d < 0.05:
                continue
            a = self.theta + p["angle"]
            ex, ey = self.x + d * math.cos(a), self.y + d * math.sin(a)
            # nearest wall to the endpoint, only if clearly closer than any other wall
            cands = [(abs(ex), "x", 0.0), (abs(ex - self.width), "x", self.width),
                     (abs(ey), "y", 0.0), (abs(ey - self.height), "y", self.height)]
            cands.sort()
            if cands[0][0] > 0.6 or cands[1][0] - cands[0][0] < 0.4:
                continue                                    # ambiguous: probably an obstacle, not a wall
            _, axis, wall = cands[0]
            if axis == "x":
                sx += wall - ex
            else:
                sy += wall - ey
            n += 1
        if n:
            k = self.p_pos / (self.p_pos + self.r_wall)
            self.x += self.wall_gain * k * sx / n
            self.y += self.wall_gain * k * sy / n
            self.p_pos *= (1 - 0.5 * k)
            self.wall_fixes += 1

    def fix(self, x: float, y: float, theta: float | None = None) -> None:
        """Absolute fix, e.g. docking on the charger."""
        self.x, self.y = x, y
        if theta is not None:
            self.theta = theta
        self.p_pos = 0.01

    def pose(self) -> FusedPose:
        return FusedPose(self.x, self.y, self.theta, self.p_pos)
