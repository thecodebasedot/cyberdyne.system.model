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
class Actor:
    """A person or object in the world. People follow a looping route."""
    id: str
    kind: str                       # "person" | "object"
    x: float
    y: float
    signature: str = ""             # stands in for a face / voice embedding
    route: list[tuple[float, float]] = field(default_factory=list)
    speed: float = 0.5
    radius: float = 0.3
    active_from: float = 0.0        # appears at this sim time
    _route_idx: int = 0

    def bbox(self) -> Obstacle:
        return Obstacle(self.x - self.radius, self.y - self.radius, 2 * self.radius, 2 * self.radius, self.id)

    def step(self, dt: float, robot: Pose | None = None, robot_radius: float = 0.2) -> None:
        if not self.route:
            return
        tx, ty = self.route[self._route_idx]
        d = math.hypot(tx - self.x, ty - self.y)
        if d < 0.05:
            self._route_idx = (self._route_idx + 1) % len(self.route)
            return
        stepd = min(d, self.speed * dt)
        nx, ny = self.x + (tx - self.x) / d * stepd, self.y + (ty - self.y) / d * stepd
        if robot is not None and robot.distance_to(nx, ny) < self.radius + robot_radius + 0.1:
            return                                  # people wait for the robot instead of walking into it
        self.x, self.y = nx, ny

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "x": self.x, "y": self.y, "radius": self.radius}


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
    actors: list[Actor] = field(default_factory=list)

    # -- geometry ---------------------------------------------------------
    def in_bounds(self, x: float, y: float) -> bool:
        r = self.robot_radius
        return r <= x <= self.width - r and r <= y <= self.height - r

    def active_actors(self) -> list[Actor]:
        return [a for a in self.actors if a.active_from <= self.time]

    def blocked(self, x: float, y: float) -> bool:
        if not self.in_bounds(x, y):
            return True
        if any(o.contains(x, y, self.robot_radius) for o in self.obstacles):
            return True
        return any(math.hypot(a.x - x, a.y - y) < a.radius + self.robot_radius for a in self.active_actors())

    def raycast(self, x: float, y: float, angle: float, max_range: float, *, static_only: bool = False) -> float:
        dx, dy = math.cos(angle), math.sin(angle)
        best = max_range
        if not static_only:
            for a in self.active_actors():
                t = _ray_rect(x, y, dx, dy, a.bbox())
                if t is not None:
                    best = min(best, t)
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
    def visible(self, actor: Actor, fov: float, max_range: float) -> tuple[bool, float, float]:
        """(visible, bearing, distance) from the robot, occluded by static obstacles only."""
        p = self.robot
        dist = p.distance_to(actor.x, actor.y)
        bearing = math.atan2(actor.y - p.y, actor.x - p.x) - p.theta
        bearing = math.atan2(math.sin(bearing), math.cos(bearing))
        if dist > max_range or abs(bearing) > fov / 2:
            return False, bearing, dist
        clear = self.raycast(p.x, p.y, p.theta + bearing, max_range, static_only=True)
        return clear >= dist - actor.radius, bearing, dist

    def step(self, dt: float) -> None:
        self.time += dt
        for a in self.active_actors():
            a.step(dt, self.robot, self.robot_radius)
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
                "actors": [a.to_dict() for a in self.active_actors()],
                "robot": self.robot.to_dict(), "cmd": self.cmd.to_dict(),
                "charger": {"x": self.charger[0], "y": self.charger[1], "r": self.charger_radius},
                "time": self.time, "collisions": self.collisions, "contacts": self.contacts,
                "distance": self.distance_travelled, "at_charger": self.at_charger()}
