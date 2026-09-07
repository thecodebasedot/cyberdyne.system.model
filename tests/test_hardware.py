"""Firmware protocol (host-compiled) and Raspberry Pi backend plumbing."""
import shutil
import subprocess
from pathlib import Path

import pytest

from cyberdyne.hal.registry import DeviceRegistry
from cyberdyne.hal.rpi.devices import ShellSpeaker, bearing_to_pixel
from cyberdyne.kernel.config import RobotConfig
from cyberdyne.runtime import Runtime
from tests.conftest import run

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("g++") is None, reason="needs g++")
def test_firmware_protocol_on_host(tmp_path):
    exe = tmp_path / "fwtest"
    subprocess.run(["g++", "-std=c++17", "-Wall", "-Werror", "-o", str(exe),
                    str(ROOT / "firmware/test/test_protocol.cpp")], check=True)
    out = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout
    assert "all checks passed" in out


def test_firmware_and_python_agree_on_the_protocol():
    """The fake firmware in Python and the C++ firmware answer the same commands the same way."""
    from cyberdyne.hal.serial import LoopbackTransport
    t = LoopbackTransport()
    for cmd, prefix in (("PING", "PONG"), ("VEL 0.1 0", "OK VEL"), ("ODOM?", "ODOM "), ("SCAN?", "SCAN "),
                        ("BATT?", "BATT "), ("NOPE", "ERR unknown")):
        t.write_line(cmd)
        assert t.read_lines()[0].startswith(prefix), cmd
    src = (ROOT / "firmware/cyberdyne_fw/protocol.h").read_text()
    for token in ("PONG cyberdyne-fw", "OK VEL", "ODOM %.4f", "SCAN", "BATT %.3f", "ERR unknown"):
        assert token in src


def test_rpi_mode_boots_degraded_without_peripherals(tmp_path):
    cfg = RobotConfig()
    cfg.kernel.mode = "rpi"
    cfg.hardware.port = "loopback"
    cfg.hardware.camera_index = -1
    cfg.hardware.vosk_model = ""
    cfg.brain.patrol = []

    async def go():
        rt = Runtime(cfg)
        results = await rt.boot()
        assert rt.state.state.value in ("idle",)                        # body devices are fine
        speaker = [d for d in rt.devices.all() if d.kind.value == "speaker"]
        assert speaker and isinstance(speaker[0], ShellSpeaker)
        ok, msg = results["speaker.shell"]
        assert ok == (speaker[0].cmd is not None)
        if not ok:
            assert rt.bus.latest_payload("kernel/degraded")["devices"] == ["speaker.shell"]
        await rt.run(1)
        await rt.shutdown()
    run(go())


def test_camera_geometry_helpers():
    assert bearing_to_pixel(0.0, 1.0, 640) == 320
    assert bearing_to_pixel(0.25, 1.0, 640) < 320 and bearing_to_pixel(-0.25, 1.0, 640) > 320
    reg = DeviceRegistry()
    assert reg.describe() == []
