import asyncio

import pytest

from cyberdyne.kernel.config import RobotConfig
from cyberdyne.runtime import Runtime


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def config() -> RobotConfig:
    cfg = RobotConfig()
    cfg.world.obstacles = [{"x": 3.0, "y": 3.0, "w": 1.5, "h": 1.0, "name": "table"}]
    cfg.brain.patrol = [{"x": 8.0, "y": 1.0}, {"x": 8.0, "y": 8.0}, {"x": 1.0, "y": 8.0}]
    return cfg


async def booted(cfg: RobotConfig) -> Runtime:
    rt = Runtime(cfg)
    await rt.boot()
    return rt
