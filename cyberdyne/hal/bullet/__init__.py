"""PyBullet backend: real rigid-body physics behind the same HAL.

    [kernel] mode = "bullet"      (pip install pybullet numpy)

The world's obstacles become boxes, actors become kinematic capsules that
follow their routes, the robot is a box with a differential-drive velocity
controller, the range sensor is a batch of ray casts and the camera reports
actors inside the field of view with occlusion by ray test. The kernel
clock drives ``stepSimulation`` in lock-step, so runs stay deterministic.
"""
from .backend import BulletWorld, build_bullet_devices

__all__ = ["BulletWorld", "build_bullet_devices"]
