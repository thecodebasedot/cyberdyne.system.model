"""MotionController: global A* path + local gap-based steering.

    in : nav/goal {x, y, name?}, nav/cancel, sensor/odometry, perception/scan
    out: motion/cmd {linear, angular}, nav/path, nav/status, nav/arrived, nav/recovery

Global: ``GridPlanner`` plans over the world-model occupancy grid (unknown =
free) and replans every ``replan_interval`` seconds or after a recovery.
Local: every scan beam is a candidate heading scored by (a) deviation from
the direction to the current path waypoint, (b) free space along the beam
and its neighbours, (c) continuity with the previously chosen heading, which
is the hysteresis that stops the robot dithering in front of a wall.
"""
from __future__ import annotations

import math

from ..kernel.context import Context
from ..kernel.module import Module
from .planner import GridPlanner


def _wrap(a: float) -> float:
    return math.atan2(math.sin(a), math.cos(a))


class MotionController(Module):
    name = "motion"
    rate_hz = 20.0
    priority = 40
    clear_needed = 1.2        # metres of free space a heading should have
    width_beams = 1           # neighbours per side that count as the robot's width
    lookahead = 0.7           # metres: path waypoint the local controller aims at
    replan_interval = 2.0

    def __init__(self, planner: GridPlanner | None = None) -> None:
        super().__init__()
        self.planner = planner or GridPlanner()

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        self.goal: dict | None = None
        self.path: list[tuple[float, float]] = []
        self.tolerance = ctx.config.brain.goal_tolerance
        self.max_lin = ctx.config.safety.max_linear
        self.max_ang = ctx.config.safety.max_angular
        self._stuck_ticks = 0
        self._backoff_ticks = 0
        self._last_plan_at = -math.inf
        self._commit: float | None = None       # world-frame heading we committed to
        self._subs = [ctx.bus.subscribe("nav/goal", self._on_goal, name="motion.goal"),
                      ctx.bus.subscribe("nav/cancel", self._on_cancel, name="motion.cancel")]

    async def teardown(self) -> None:
        for s in self._subs:
            self.ctx.bus.unsubscribe(s)

    def _on_goal(self, msg) -> None:
        self.goal = dict(msg.payload)
        self.path = []
        self._stuck_ticks = self._backoff_ticks = 0
        self._last_plan_at = -math.inf
        self._commit = None

    def _on_cancel(self, msg) -> None:
        self.goal = None
        self.path = []

    # -- global ----------------------------------------------------------------
    async def _replan(self, pose: dict) -> None:
        wm = self.ctx.extras.get("world_model")
        self._last_plan_at = self.ctx.now
        if wm is None or self.goal is None:
            self.path = []
            return
        space = self.ctx.config.social.personal_space
        people = [(p["x"], p["y"], space, 6.0) for p in self.ctx.bus.latest_payload("perception/people", []) or []]
        self.path = self.planner.plan(wm.grid, (pose["x"], pose["y"]), (self.goal["x"], self.goal["y"]), people)
        await self.ctx.bus.publish("nav/path", {"points": self.path, "goal": self.goal}, source=self.name)

    def _local_target(self, pose: dict) -> tuple[float, float]:
        px, py = pose["x"], pose["y"]
        while len(self.path) > 1 and math.hypot(self.path[0][0] - px, self.path[0][1] - py) < self.lookahead:
            self.path.pop(0)
        if self.path:
            return self.path[0]
        return self.goal["x"], self.goal["y"]

    # -- local --------------------------------------------------------------------
    def _choose_heading(self, scan: list[dict], heading_err: float, dist: float, theta: float
                        ) -> tuple[float, float]:
        if not scan:
            return heading_err, self.clear_needed
        n = len(scan)
        best, best_cost, best_free = heading_err, math.inf, 0.0
        need = min(self.clear_needed, dist + 0.3)
        for i, p in enumerate(scan):
            lo, hi = max(0, i - self.width_beams), min(n, i + self.width_beams + 1)
            free = min(q["distance"] for q in scan[lo:hi])
            blocked = max(0.0, 1.0 - free / need)
            cost = abs(_wrap(p["angle"] - heading_err)) + 4.0 * blocked ** 2
            if self._commit is not None:
                cost += 0.6 * abs(_wrap(theta + p["angle"] - self._commit))
            if cost < best_cost:
                best, best_cost, best_free = p["angle"], cost, free
        self._commit = theta + best
        return best, best_free

    async def tick(self, dt: float) -> None:
        bus = self.ctx.bus
        if self.goal is None:
            await bus.publish("motion/cmd", {"linear": 0.0, "angular": 0.0}, source=self.name)
            await bus.publish("nav/status", {"state": "idle"}, source=self.name)
            return
        pose = bus.latest_payload("sensor/odometry")
        if pose is None:
            return
        gx, gy = self.goal["x"], self.goal["y"]
        dist = math.hypot(gx - pose["x"], gy - pose["y"])
        if dist <= self.tolerance:
            goal, self.goal, self.path = self.goal, None, []
            await bus.publish("motion/cmd", {"linear": 0.0, "angular": 0.0}, source=self.name)
            await bus.publish("nav/arrived", goal, source=self.name)
            await bus.publish("nav/status", {"state": "arrived", "goal": goal}, source=self.name)
            return

        if self.ctx.now - self._last_plan_at >= self.replan_interval:
            await self._replan(pose)
        tx, ty = self._local_target(pose)
        tdist = math.hypot(tx - pose["x"], ty - pose["y"])
        heading_err = _wrap(math.atan2(ty - pose["y"], tx - pose["x"]) - pose["theta"])
        scan = bus.latest_payload("perception/scan", []) or []
        steer, free = self._choose_heading(scan, heading_err, tdist, pose["theta"])

        ang = max(-self.max_ang, min(self.max_ang, 2.5 * steer))
        speed_scale = min(1.0, free / self.clear_needed) * max(0.0, math.cos(steer))
        # social: slow down inside anyone's personal space
        space = self.ctx.config.social.personal_space
        for person in bus.latest_payload("perception/people", []) or []:
            d = math.hypot(person["x"] - pose["x"], person["y"] - pose["y"])
            if d < space * 1.5:
                speed_scale *= max(0.25, d / (space * 1.5))
        lin = min(self.max_lin * speed_scale, max(0.15, dist))
        if abs(steer) > math.radians(60):          # turn in place first
            lin = 0.0

        moving = abs(pose["linear"]) > 1e-3
        self._stuck_ticks = 0 if moving or lin < 0.05 else self._stuck_ticks + 1
        if self._backoff_ticks > 0:
            self._backoff_ticks -= 1
            lin, ang = -0.25, self.max_ang * (1 if steer >= 0 else -1)
        elif self._stuck_ticks > self.rate_hz * 1.5:
            self._stuck_ticks = 0
            self._backoff_ticks = int(self.rate_hz * 1.0)
            self._last_plan_at = -math.inf          # force a replan after backing off
            self._commit = None
            await bus.publish("nav/recovery", {"pose": pose, "goal": self.goal}, source=self.name)

        await bus.publish("motion/cmd", {"linear": lin, "angular": ang}, source=self.name)
        await bus.publish("nav/status", {"state": "moving", "goal": self.goal, "distance": round(dist, 3),
                                         "free": round(free, 2), "path_left": len(self.path)},
                          source=self.name)
