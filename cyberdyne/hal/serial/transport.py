from __future__ import annotations

import math
from abc import ABC, abstractmethod


class LineTransport(ABC):
    @abstractmethod
    def write_line(self, line: str) -> None: ...

    @abstractmethod
    def read_lines(self) -> list[str]: ...

    def close(self) -> None:  # noqa: B027
        pass


class SerialTransport(LineTransport):
    """pyserial-backed transport (optional dependency: ``pip install pyserial``)."""

    def __init__(self, port: str, baud: int = 115200, timeout: float = 0.0) -> None:
        try:
            import serial
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("pyserial not installed: pip install pyserial") from exc
        self._ser = serial.Serial(port, baud, timeout=timeout)
        self._buf = b""

    def write_line(self, line: str) -> None:
        self._ser.write((line + "\n").encode())

    def read_lines(self) -> list[str]:
        self._buf += self._ser.read(self._ser.in_waiting or 0)
        *lines, self._buf = self._buf.split(b"\n")
        return [x.decode(errors="replace").strip() for x in lines if x.strip()]

    def close(self) -> None:
        self._ser.close()


class LoopbackTransport(LineTransport):
    """Fake firmware: integrates a differential drive and answers the protocol.

    Deliberately imperfect (``wheel_scale``) so calibration has something to
    find. ``advance(dt)`` moves simulated time; the runtime's serial stepper
    calls it from the kernel clock.
    """

    def __init__(self, wheel_scale: float = 1.0, beams: int = 8, wall_distance: float = 3.0) -> None:
        self.wheel_scale = wheel_scale
        self.beams, self.wall = beams, wall_distance
        self.x = self.y = self.th = 0.0
        self.v = self.w = 0.0
        self.batt = 0.9
        self._out: list[str] = []
        self.received: list[str] = []

    def advance(self, dt: float) -> None:
        self.th += self.w * self.wheel_scale * dt
        self.x += self.v * self.wheel_scale * math.cos(self.th) * dt
        self.y += self.v * self.wheel_scale * math.sin(self.th) * dt
        self.batt = max(0.0, self.batt - 0.0002 * dt * (1 + abs(self.v)))

    def write_line(self, line: str) -> None:
        self.received.append(line)
        parts = line.split()
        if not parts:
            return
        cmd = parts[0]
        if cmd == "VEL" and len(parts) == 3:
            self.v, self.w = float(parts[1]), float(parts[2])
            self._out.append("OK VEL")
        elif cmd == "ODOM?":
            self._out.append(f"ODOM {self.x:.4f} {self.y:.4f} {self.th:.4f} {self.v:.3f} {self.w:.3f}")
        elif cmd == "SCAN?":
            pts = []
            for i in range(self.beams):
                a = -math.pi / 2 + math.pi * i / (self.beams - 1)
                pts.append(f"{a:.3f}:{self.wall:.2f}")
            self._out.append("SCAN " + ",".join(pts))
        elif cmd == "BATT?":
            self._out.append(f"BATT {self.batt:.3f} {10.5 + 2.1 * self.batt:.2f} 0")
        elif cmd == "PING":
            self._out.append("PONG cyberdyne-fw 1.0")
        else:
            self._out.append(f"ERR unknown {cmd}")

    def read_lines(self) -> list[str]:
        out, self._out = self._out, []
        return out
