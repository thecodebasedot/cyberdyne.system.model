"""Test harness for skill and module authors.

    from cyberdyne.testing import robot

    async def test_my_skill():
        async with robot(patrol=False) as rt:
            res = await rt.invoke("wave", {"who": "Rafi"})
            assert res.ok

``robot()`` boots a headless simulated robot with a mock world, a strict
bus, and helpers to invoke skills, say things, and wait for topics.
"""
from .harness import TestRobot, robot, run

__all__ = ["TestRobot", "robot", "run"]
