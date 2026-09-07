"""Raspberry Pi backend: serial bridge for the body + real camera, mic and speaker.

Every driver imports its heavy dependency lazily and degrades to a clear
error, so ``mode = "rpi"`` boots on a bare Pi and the diagnostic tells you
which peripheral is missing. See docs/HARDWARE.md for the install steps.
"""
from .devices import OpenCVCamera, ShellSpeaker, VoskMicrophone, build_rpi_devices

__all__ = ["OpenCVCamera", "ShellSpeaker", "VoskMicrophone", "build_rpi_devices"]
