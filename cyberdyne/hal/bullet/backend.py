from __future__ import annotations

import math
import random

from ...kernel.config import WorldConfig
from ...kernel.module import Module
from ...sim.world import Actor, Obstacle, Pose, Twist, World
from ..interfaces import (
    IMU,
    Battery,
    BatteryReading,
    Camera,
    Detection,
    DriveBase,
    Frame,
    IMUReading,
    Microphone,
    Odometry,
    RangeSensor,
    ScanPoint,
    Speaker,
    Utterance,
)
from ..registry import DeviceRegistry


class BulletWorld:
    """Owns the pybullet client. Mirrors pose/actors into a ``sim.World`` so the
    dashboard, mental simulator and tests can keep using the 2D view."""

    def __init__(self, cfg: WorldConfig, actors: list[Actor], timestep: float = 1 / 240) -> None:
        import pybullet as p
        self.p = p
        self.cfg = cfg
        self.client = p.connect(p.DIRECT)
        p.setGravity(0, 0, -9.81, physicsClientId=self.client)
        p.setTimeStep(timestep, physicsClientId=self.client)
        self.timestep = timestep
        self.mirror = World(width=cfg.width, height=cfg.height, obstacles=[Obstacle(**o) for o in cfg.obstacles],
                            robot=Pose(**cfg.robot_start), charger=(cfg.charger["x"], cfg.charger["y"]), actors=actors)
        self._ground = self._box((cfg.width / 2, cfg.height / 2, -0.05), (cfg.width / 2, cfg.height / 2, 0.05), 0.0)
        wall_t = 0.1
        for cx, cy, hx, hy in ((cfg.width / 2, -wall_t, cfg.width / 2 + wall_t, wall_t),
                               (cfg.width / 2, cfg.height + wall_t, cfg.width / 2 + wall_t, wall_t),
                               (-wall_t, cfg.height / 2, wall_t, cfg.height / 2),
                               (cfg.width + wall_t, cfg.height / 2, wall_t, cfg.height / 2)):
            self._box((cx, cy, 0.5), (hx, hy, 0.5), 0.0)
        self.obstacle_ids = [self._box((o.x + o.w / 2, o.y + o.h / 2, 0.4), (o.w / 2, o.h / 2, 0.4), 0.0)
                             for o in self.mirror.obstacles]
        self.actor_ids: dict[str, int] = {}
        for a in actors:
            self.actor_ids[a.id] = self._box((a.x, a.y, 0.8 if a.kind == "person" else 0.15),
                                             (a.radius, a.radius, 0.8 if a.kind == "person" else 0.15), 0.0)
        r = cfg.robot_start
        self.robot_id = self._box((r["x"], r["y"], 0.15), (0.18, 0.15, 0.1), 5.0)
        orn = p.getQuaternionFromEuler((0, 0, r.get("theta", 0.0)))
        p.resetBasePositionAndOrientation(self.robot_id, (r["x"], r["y"], 0.15), orn, physicsClientId=self.client)
        p.changeDynamics(self.robot_id, -1, lateralFriction=0.6, physicsClientId=self.client)
        self.cmd = Twist()
        self.time = 0.0
        self.collisions = 0

    def _box(self, pos, half, mass: float) -> int:
        p = self.p
        col = p.createCollisionShape(p.GEOM_BOX, halfExtents=half, physicsClientId=self.client)
        return p.createMultiBody(mass, col, basePosition=pos, physicsClientId=self.client)

    # -- state ------------------------------------------------------------------
    def pose(self) -> Pose:
        pos, orn = self.p.getBasePositionAndOrientation(self.robot_id, physicsClientId=self.client)
        yaw = self.p.getEulerFromQuaternion(orn)[2]
        return Pose(pos[0], pos[1], yaw)

    def step(self, dt: float) -> None:
        p = self.p
        n = max(1, int(round(dt / self.timestep)))
        for _ in range(n):
            pose = self.pose()
            # velocity-controlled differential drive: set planar velocity in the body frame
            vx, vy = self.cmd.linear * math.cos(pose.theta), self.cmd.linear * math.sin(pose.theta)
            p.resetBaseVelocity(self.robot_id, (vx, vy, 0.0), (0.0, 0.0, self.cmd.angular), physicsClientId=self.client)
            for a in self.mirror.active_actors():
                a.step(self.timestep, self.mirror.robot, self.mirror.robot_radius)
                p.resetBasePositionAndOrientation(self.actor_ids[a.id], (a.x, a.y, 0.8 if a.kind == "person" else 0.15),
                                                  (0, 0, 0, 1), physicsClientId=self.client)
            p.stepSimulation(physicsClientId=self.client)
            self.time += self.timestep
        pose = self.pose()
        self.mirror.robot = pose
        self.mirror.cmd = self.cmd
        self.mirror.time = self.time
        pts = p.getContactPoints(bodyA=self.robot_id, physicsClientId=self.client)
        if any(c[2] != self._ground for c in pts):
            self.collisions += 1
            self.mirror.collisions = self.collisions

    def raycast(self, angles: list[float], max_range: float) -> list[float]:
        pose = self.pose()
        z = 0.2
        froms = [(pose.x, pose.y, z)] * len(angles)
        tos = [(pose.x + max_range * math.cos(pose.theta + a), pose.y + max_range * math.sin(pose.theta + a), z)
               for a in angles]
        out = []
        for hit in self.p.rayTestBatch(froms, tos, physicsClientId=self.client):
            body, frac = hit[0], hit[2]
            out.append(max_range if body < 0 or body == self.robot_id else frac * max_range)
        return out

    def visible(self, actor: Actor, fov: float, max_range: float) -> tuple[bool, float, float]:
        pose = self.pose()
        dist = pose.distance_to(actor.x, actor.y)
        bearing = math.atan2(actor.y - pose.y, actor.x - pose.x) - pose.theta
        bearing = math.atan2(math.sin(bearing), math.cos(bearing))
        if dist > max_range or abs(bearing) > fov / 2:
            return False, bearing, dist
        hit = self.p.rayTest((pose.x, pose.y, 0.3), (actor.x, actor.y, 0.3), physicsClientId=self.client)[0]
        return hit[0] in (-1, self.actor_ids[actor.id]), bearing, dist

    def close(self) -> None:
        self.p.disconnect(physicsClientId=self.client)


class BulletDrive(DriveBase):
    device_id = "drive.bullet"

    def __init__(self, bw: BulletWorld) -> None:
        self.bw = bw

    async def set_velocity(self, linear: float, angular: float) -> None:
        self.bw.cmd = Twist(linear, angular)

    async def odometry(self) -> Odometry:
        p = self.bw.pose()
        return Odometry(p.x, p.y, p.theta, self.bw.cmd.linear, self.bw.cmd.angular)


class BulletRange(RangeSensor):
    device_id = "range.bullet"

    def __init__(self, bw: BulletWorld, beams: int = 16, fov: float = math.pi, max_range: float = 4.0) -> None:
        self.bw, self.beams, self.fov, self.max_range = bw, beams, fov, max_range
        self.angles = [-fov / 2 + fov * i / (beams - 1) for i in range(beams)]

    async def scan(self) -> list[ScanPoint]:
        return [ScanPoint(a, d) for a, d in zip(self.angles, self.bw.raycast(self.angles, self.max_range), strict=True)]


class BulletCamera(Camera):
    device_id = "camera.bullet"

    def __init__(self, bw: BulletWorld, fov: float = 1.2, max_range: float = 6.0, seed: int = 1) -> None:
        self.bw, self.fov, self.max_range = bw, fov, max_range
        self._rng = random.Random(seed)

    async def capture(self) -> Frame:
        dets = []
        for a in self.bw.mirror.active_actors():
            vis, bearing, dist = self.bw.visible(a, self.fov, self.max_range)
            if vis:
                conf = max(0.3, 1.0 - dist / self.max_range)
                dets.append(Detection(a.kind, a.kind, bearing, dist, round(conf, 2), a.signature if conf > 0.5 else ""))
        return Frame(self.bw.time, 640, 480, dets)


class BulletBattery(Battery):
    device_id = "battery.bullet"

    def __init__(self, bw: BulletWorld, cfg: WorldConfig) -> None:
        self.bw, self.cfg, self.level, self._t = bw, cfg, cfg.battery_start, 0.0

    async def read(self) -> BatteryReading:
        dt, self._t = self.bw.time - self._t, self.bw.time
        charging = self.bw.mirror.at_charger() and abs(self.bw.cmd.linear) < 1e-6
        if dt > 0:
            if charging:
                self.level = min(1.0, self.level + self.cfg.battery_charge_rate * dt)
            else:
                speed = abs(self.bw.cmd.linear) + 0.3 * abs(self.bw.cmd.angular)
                drain = self.cfg.battery_drain_idle + self.cfg.battery_drain_moving * speed
                self.level = max(0.0, self.level - drain * dt)
        return BatteryReading(self.level, charging, 10.5 + 2.1 * self.level)


class BulletIMU(IMU):
    device_id = "imu.bullet"

    def __init__(self, bw: BulletWorld) -> None:
        self.bw = bw

    async def read(self) -> IMUReading:
        return IMUReading(self.bw.pose().theta, self.bw.cmd.angular)


class BulletMic(Microphone):
    device_id = "mic.bullet"

    def __init__(self, bw: BulletWorld) -> None:
        self.bw, self._q = bw, []

    def inject(self, text: str, signature: str = "", loudness: float = 1.0) -> None:
        self._q.append(Utterance(text, self.bw.time, signature, loudness))

    async def listen(self) -> list[Utterance]:
        out, self._q = self._q, []
        return out


class BulletSpeaker(Speaker):
    device_id = "speaker.bullet"

    def __init__(self) -> None:
        self.spoken: list[tuple[str, str]] = []

    async def say(self, text: str, voice: str = "neutral") -> None:
        self.spoken.append((voice, text))


class BulletStepper(Module):
    name = "bullet_physics"
    rate_hz = 60.0
    priority = 2

    def __init__(self, bw: BulletWorld) -> None:
        super().__init__()
        self.bw = bw

    async def setup(self, ctx) -> None:
        self.ctx = ctx
        self._sub = ctx.bus.subscribe("sim/hear", self._on_hear, name="bullet.hear")

    async def teardown(self) -> None:
        self.ctx.bus.unsubscribe(self._sub)

    def _on_hear(self, msg) -> None:
        p = msg.payload or {}
        for dev in self.ctx.devices.all():
            if hasattr(dev, "inject"):
                dev.inject(str(p.get("text", "")), str(p.get("signature", "")), float(p.get("loudness", 1.0)))

    async def tick(self, dt: float) -> None:
        self.bw.step(dt)


def build_bullet_devices(cfg: WorldConfig, actors: list[Actor], registry: DeviceRegistry) -> BulletWorld:
    bw = BulletWorld(cfg, actors)
    registry.register(BulletDrive(bw))
    registry.register(BulletRange(bw))
    registry.register(BulletBattery(bw, cfg))
    registry.register(BulletIMU(bw))
    registry.register(BulletCamera(bw))
    registry.register(BulletMic(bw))
    registry.register(BulletSpeaker())
    return bw
