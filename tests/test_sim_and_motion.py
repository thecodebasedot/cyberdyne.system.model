import math

from cyberdyne.motion.planner import GridPlanner
from cyberdyne.sim.world import Obstacle, Pose, Twist, World
from cyberdyne.world_model.grid import OccupancyGrid


def test_raycast_hits_obstacle_and_walls():
    w = World(10, 10, [Obstacle(4, 0, 1, 10)], Pose(1, 5, 0))
    assert math.isclose(w.raycast(1, 5, 0.0, 10), 3.0)
    assert math.isclose(w.raycast(1, 5, math.pi, 10), 1.0)
    assert math.isclose(w.raycast(1, 5, math.pi / 2, 10), 5.0)
    assert w.raycast(1, 5, 0.0, 2.0) == 2.0


def test_step_integrates_and_slides():
    w = World(10, 10, [Obstacle(2, 0, 1, 10)], Pose(1.0, 5.0, 0.0), cmd=Twist(1.0, 0.0))
    for _ in range(200):
        w.step(0.01)
    assert w.robot.x < 1.81 and w.collisions > 0          # stopped at the inflated wall
    w.cmd = Twist(1.0, 0.0)
    w.robot.theta = math.pi / 4                           # diagonal into the wall -> slide up
    y0 = w.robot.y
    for _ in range(100):
        w.step(0.01)
    assert w.robot.y > y0 and w.contacts > 0


def test_planner_routes_around_known_obstacle():
    g = OccupancyGrid(10, 10, 0.5)
    for r in range(2, 16):                                # wall x=[5,5.5) y=[1,8)
        g.cells[r * g.cols + 10] = 5.0
    path = GridPlanner(inflate=1).plan(g, (1.0, 4.0), (9.0, 4.0))
    assert path and path[-1] == (9.0, 4.0)
    assert all(not (4.5 <= x < 6.0) or y >= 8.0 or y < 1.0 for x, y in path)
    assert GridPlanner().plan(g, (1.0, 4.0), (50.0, 4.0)) == []
