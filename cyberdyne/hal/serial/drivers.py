from __future__ import annotations

from ...kernel.config import HardwareConfig
from ..interfaces import Battery, BatteryReading, DriveBase, Odometry, RangeSensor, ScanPoint
from ..registry import DeviceRegistry
from .transport import LineTransport, LoopbackTransport, SerialTransport


class SerialBridge:
    """Request/response over the line protocol, shared by all serial devices."""

    def __init__(self, transport: LineTransport) -> None:
        self.t = transport
        self.errors = 0
        self._last: dict[str, str] = {}

    def query(self, cmd: str) -> str | None:
        self.t.write_line(cmd)
        reply = None
        for line in self.t.read_lines():
            key = line.split(" ", 1)[0]
            self._last[key] = line
            if line.startswith("ERR"):
                self.errors += 1
            reply = line
        return reply

    def last(self, key: str) -> str | None:
        return self._last.get(key)

    def ping(self) -> bool:
        r = self.query("PING")
        return bool(r and r.startswith("PONG"))


class SerialDrive(DriveBase):
    device_id = "drive.serial"

    def __init__(self, bridge: SerialBridge, cfg: HardwareConfig) -> None:
        self.bridge, self.cfg = bridge, cfg

    async def set_velocity(self, linear: float, angular: float) -> None:
        self.bridge.query(f"VEL {linear / self.cfg.linear_scale:.3f} {angular / self.cfg.angular_scale:.3f}")

    async def odometry(self) -> Odometry:
        r = self.bridge.query("ODOM?") or self.bridge.last("ODOM")
        if not r or not r.startswith("ODOM"):
            raise OSError("no odometry from firmware")
        _, x, y, th, v, w = r.split()
        s, a = self.cfg.linear_scale, self.cfg.angular_scale
        return Odometry(float(x) * s, float(y) * s, float(th) * a, float(v) * s, float(w) * a)

    async def self_test(self) -> tuple[bool, str]:
        return (True, "firmware answered") if self.bridge.ping() else (False, "no PONG from firmware")


class SerialRangeSensor(RangeSensor):
    device_id = "range.serial"

    def __init__(self, bridge: SerialBridge, max_range: float = 4.0) -> None:
        self.bridge, self.max_range = bridge, max_range

    async def scan(self) -> list[ScanPoint]:
        r = self.bridge.query("SCAN?") or self.bridge.last("SCAN")
        if not r or not r.startswith("SCAN "):
            raise OSError("no scan from firmware")
        pts = []
        for item in r[5:].split(","):
            a, d = item.split(":")
            pts.append(ScanPoint(float(a), min(self.max_range, float(d))))
        return pts


class SerialBattery(Battery):
    device_id = "battery.serial"

    def __init__(self, bridge: SerialBridge) -> None:
        self.bridge = bridge

    async def read(self) -> BatteryReading:
        r = self.bridge.query("BATT?") or self.bridge.last("BATT")
        if not r or not r.startswith("BATT"):
            raise OSError("no battery reading from firmware")
        _, level, volt, charging = r.split()
        return BatteryReading(float(level), charging == "1", float(volt))


def build_serial_devices(cfg: HardwareConfig, registry: DeviceRegistry,
                         transport: LineTransport | None = None) -> SerialBridge:
    if transport is None:
        transport = LoopbackTransport() if cfg.port == "loopback" else SerialTransport(cfg.port, cfg.baud)
    bridge = SerialBridge(transport)
    registry.register(SerialDrive(bridge, cfg))
    registry.register(SerialRangeSensor(bridge))
    registry.register(SerialBattery(bridge))
    return bridge
