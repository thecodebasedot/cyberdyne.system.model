"""Device registry: the only place modules look up hardware."""
from __future__ import annotations

from typing import TypeVar

from .interfaces import Device, DeviceKind

T = TypeVar("T", bound=Device)


class DeviceRegistry:
    def __init__(self) -> None:
        self._devices: dict[DeviceKind, list[Device]] = {}

    def register(self, device: Device) -> Device:
        self._devices.setdefault(device.kind, []).append(device)
        return device

    def get(self, kind: DeviceKind, cls: type[T] | None = None) -> T:
        devs = self._devices.get(kind) or []
        if not devs:
            raise LookupError(f"no device registered for {kind.value}")
        dev = devs[0]
        if cls is not None and not isinstance(dev, cls):
            raise TypeError(f"{kind.value} device is {type(dev).__name__}, expected {cls.__name__}")
        return dev  # type: ignore[return-value]

    def has(self, kind: DeviceKind) -> bool:
        return bool(self._devices.get(kind))

    def all(self) -> list[Device]:
        return [d for ds in self._devices.values() for d in ds]

    async def open_all(self) -> None:
        for d in self.all():
            await d.open()

    async def close_all(self) -> None:
        for d in self.all():
            await d.close()

    async def self_test_all(self) -> dict[str, tuple[bool, str]]:
        return {d.device_id: await d.self_test() for d in self.all()}

    def describe(self) -> list[dict]:
        return [d.describe() for d in self.all()]
