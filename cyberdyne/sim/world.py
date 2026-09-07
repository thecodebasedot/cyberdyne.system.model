"""Minimal 2D physics world.

Axis-aligned rectangular obstacles, a single differential-drive robot,
ray casting for range sensors, and a charging station. Enough to exercise
every layer above the HAL without any external physics engine; the HAL
interfaces are designed so this can later be swapped for PyBullet/MuJoCo.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Pose:
    x: float = 0.0
    y: float = 0.0
    theta: float = 0.0

    def distance_to(self, x: float, y: float) -> float:
        return math.hypot(self.x - x, self.y - y)

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "theta": self.theta}


@dataclass
class Twist:
    linear: float = 0.0
    angular: float = 0.0

    def to_dict(self) -> dict:
        return {"linear": self.linear, "angular": self.angular}


@dataclass
class Obstacle:
    x: float
    y: float
    w: float
    h: float
    name: str = ""

    def contains(self, px: float, py: float, margin: float = 0.0) -> bool:
        return (self.x - margin <= px <= self.x + self.w + margin and
                self.y - margin <= py <= self.y + self.h + margin)

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h, "name": self.name}


def _ray_rect(ox: float, oy: float, dx: float, dy: float, r: Obstacle) -> float | None:
    """Slab-method ray/AABB intersection. Returns entry distance or None."""
    tmin, tmax = 0.0, math.inf
    for o, d, lo, hi in ((ox, dx, r.x, r.x + r.w), (oy, dy, r.y, r.y + r.h)):
        if abs(d) < 1e-12:
            if o < lo or o > hi:
                return None
            continue
        t1, t2 = (lo - o) / d, (hi - o) / d
        if t1 > t2:
            t1, t2 = t2, t1
        tmin, tmax = max(tmin, t1), min(tmax, t2)
        if tmin > tmax:
            return None
    return tmin if tmin >= 0 else None


@dataclass
class World:
    width: float = 10.0
    height: float = 10.0
    obstacles: list[Obstacle] = field(default_factory=list)
    robot: Pose = field(default_factory=Pose)
    cmd: Twist = field(default_factory=Twist)
    charger: tuple[float, float] = (0.5, 0.5)
    charger_radius: float = 0.4
    robot_radius: float = 0.2
    time: float = 0.0
    collisions: int = 0          # fully blocked steps
    contacts: int = 0            # steps that slid along a surface
    distance_travelled: float = 0.0
    last_collision: bool = False

    # -- geometry ---------------------------------------------------------
    def in_bounds(self, x: float, y: float) -> bool:
        r = self.robot_radius
        return r <= x <= self.width - r and r <= y <= self.height - r

    def blocked(self, x: float, y: float) -> bool:
        if not self.in_bounds(x, y):
            return True
        return any(o.contains(x, y, self.robot_radius) for o in self.obstacles)

    def raycast(self, x: float, y: float, angle: float, max_range: float) -> float:
        dx, dy = math.cos(angle), math.sin(angle)
        best = max_range
        # walls
        for wall in (Obstacle(-1, -1, 1, self.height + 2), Obstacle(self.width, -1, 1, self.height + 2),
                     Obstacle(-1, -1, self.width + 2, 1), Obstacle(-1, self.height, self.width + 2, 1)):
            t = _ray_rect(x, y, dx, dy, wall)
            if t is not None:
                best = min(best, t)
        for o in self.obstacles:
            t = _ray_rect(x, y, dx, dy, o)
            if t is not None:
                best = min(best, t)
        return best

    def at_charger(self) -> bool:
        return self.robot.distance_to(*self.charger) <= self.charger_radius

    # -- integration ------------------------------------------------------
    def step(self, dt: float) -> None:
        self.time += dt
        p, c = self.robot, self.cmd
        theta = p.theta + c.angular * dt
        nx = p.x + c.linear * math.cos(theta) * dt
        ny = p.y + c.linear * math.sin(theta) * dt
        p.theta = math.atan2(math.sin(theta), math.cos(theta))
        if self.blocked(nx, ny):
            # try to slide along the surface before giving up (a slide must actually move)
            if abs(nx - p.x) > 1e-9 and not self.blocked(nx, p.y):
                ny = p.y
            elif abs(ny - p.y) > 1e-9 and not self.blocked(p.x, ny):
                nx = p.x
            else:
                self.collisions += 1
                self.last_collision = True
                return
            self.contacts += 1
        self.distance_travelled += math.hypot(nx - p.x, ny - p.y)
        p.x, p.y = nx, ny
        self.last_collision = False

    def to_dict(self) -> dict:
        return {"width": self.width, "height": self.height,
                "obstacles": [o.to_dict() for o in self.obstacles],
                "robot": self.robot.to_dict(), "cmd": self.cmd.to_dict(),
                "charger": {"x": self.charger[0], "y": self.charger[1], "r": self.charger_radius},
                "time": self.time, "collisions": self.collisions, "contacts": self.contacts,
                "distance": self.distance_travelled, "at_charger": self.at_charger()}
