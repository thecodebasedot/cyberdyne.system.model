"""Mental simulation: try a plan in an imagined world before acting.

The imagined world is built only from what the robot *believes* (the
occupancy grid), never from ground truth, so the prediction is honest about
what the robot does not know. Each goto step is rolled out with a small
kinematic follower on the A* path; the result is a prediction, not a promise.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..kernel.config import RobotConfig
from ..motion.planner import GridPlanner
from ..sim.world import Obstacle, Pose, Twist, World
from ..world_model.grid import OccupancyGrid
from .planner import Plan


@dataclass
class StepOutcome:
    step: int
    skill: str
    reachable: bool
    time: float = 0.0
    distance: float = 0.0
    battery_after: float = 1.0
    note: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Rollout:
    outcomes: list[StepOutcome] = field(default_factory=list)
    total_time: float = 0.0
    total_distance: float = 0.0
    battery_after: float = 1.0

    @property
    def feasible(self) -> bool:
        return all(o.reachable for o in self.outcomes)

    def to_dict(self) -> dict:
        return {"feasible": self.feasible, "total_time": round(self.total_time, 1),
                "total_distance": round(self.total_distance, 2),
                "battery_after": round(self.battery_after, 3),
                "outcomes": [o.to_dict() for o in self.outcomes]}


class MentalSimulator:
    def __init__(self, config: RobotConfig, planner: GridPlanner | None = None) -> None:
        self.config = config
        self.planner = planner or GridPlanner()
        self.rollouts = 0

    def imagine_world(self, grid: OccupancyGrid, pose: dict) -> World:
        w = self.config.world
        obstacles = [Obstacle(c * grid.res, r * grid.res, grid.res, grid.res, "belief")
                     for c, r in grid.occupied_cells()]
        return World(w.width, w.height, obstacles, Pose(pose["x"], pose["y"], pose["theta"]),
                     charger=(w.charger["x"], w.charger["y"]))

    def rollout(self, plan: Plan, grid: OccupancyGrid, pose: dict, battery: float) -> Rollout:
        self.rollouts += 1
        world = self.imagine_world(grid, pose)
        cfg = self.config
        speed = cfg.safety.max_linear
        drain = cfg.world.battery_drain_moving * speed + cfg.world.battery_drain_idle
        result = Rollout(battery_after=battery)
        for i, step in enumerate(plan.steps):
            if step.skill != "goto":
                result.outcomes.append(StepOutcome(i, step.skill, True, note="non-motion step"))
                continue
            gx, gy = float(step.args["x"]), float(step.args["y"])
            path = self.planner.plan(grid, (world.robot.x, world.robot.y), (gx, gy))
            if not path:
                result.outcomes.append(StepOutcome(i, "goto", False, note="no path in believed map"))
                continue
            t, d = self._follow(world, path, speed, gx, gy)
            reached = world.robot.distance_to(gx, gy) <= cfg.brain.goal_tolerance * 1.5
            result.battery_after = max(0.0, result.battery_after - drain * t)
            result.total_time += t
            result.total_distance += d
            result.outcomes.append(StepOutcome(i, "goto", reached, round(t, 1), round(d, 2),
                                               round(result.battery_after, 3),
                                               "" if reached else "follower stalled"))
        return result

    @staticmethod
    def _follow(world: World, path: list[tuple[float, float]], speed: float,
                gx: float, gy: float, dt: float = 0.05) -> tuple[float, float]:
        straight = world.robot.distance_to(gx, gy)
        budget = 4.0 * straight / max(speed, 0.1) + 10.0
        t, d0 = 0.0, world.distance_travelled
        pts = list(path)
        while t < budget and pts:
            tx, ty = pts[0]
            if world.robot.distance_to(tx, ty) < 0.3 and len(pts) > 1:
                pts.pop(0)
                continue
            if world.robot.distance_to(gx, gy) <= 0.15:
                break
            err = math.atan2(ty - world.robot.y, tx - world.robot.x) - world.robot.theta
            err = math.atan2(math.sin(err), math.cos(err))
            world.cmd = Twist(speed * max(0.0, math.cos(err)) if abs(err) < 1.0 else 0.0,
                              max(-1.5, min(1.5, 3.0 * err)))
            world.step(dt)
            t += dt
        return t, world.distance_travelled - d0
