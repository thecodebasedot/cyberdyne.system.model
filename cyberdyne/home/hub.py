"""Smart-home devices behind one interface.

``VirtualHub`` keeps state in memory (simulation, tests). ``MQTTHub`` and
``HassHub`` are thin, optional-dependency adapters (paho-mqtt / plain HTTP)
that speak to real homes; they are wired but not exercised in CI.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..kernel.config import HomeConfig

log = logging.getLogger("cyberdyne.home")


@dataclass
class SmartDevice:
    id: str
    kind: str                 # light | fan | door | plug | ...
    room: str = ""
    name: str = ""
    state: dict[str, Any] = field(default_factory=lambda: {"on": False})

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "room": self.room, "name": self.name or self.id, "state": self.state}


class DeviceHub(ABC):
    def __init__(self, cfg: HomeConfig) -> None:
        self.devices: dict[str, SmartDevice] = {d["id"]: SmartDevice(d["id"], d.get("kind", "light"),
                                                                     d.get("room", ""), d.get("name", ""))
                                                for d in cfg.devices}
        self.commands = 0

    def find(self, kind: str | None = None, room: str | None = None, name: str | None = None) -> list[SmartDevice]:
        out = []
        for d in self.devices.values():
            if kind and d.kind != kind:
                continue
            if room and d.room.lower() != room.lower():
                continue
            if name and name.lower() not in (d.id.lower(), (d.name or "").lower()):
                continue
            out.append(d)
        return out

    async def set_state(self, device: SmartDevice, **state: Any) -> dict:
        self.commands += 1
        await self._apply(device, state)
        device.state.update(state)
        return device.state

    @abstractmethod
    async def _apply(self, device: SmartDevice, state: dict[str, Any]) -> None: ...

    def describe(self) -> list[dict]:
        return [d.to_dict() for d in self.devices.values()]


class VirtualHub(DeviceHub):
    async def _apply(self, device: SmartDevice, state: dict[str, Any]) -> None:
        pass


class MQTTHub(DeviceHub):
    """Publishes ``cyberdyne/<device>/set`` JSON; needs ``pip install paho-mqtt``."""

    def __init__(self, cfg: HomeConfig) -> None:
        super().__init__(cfg)
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("paho-mqtt not installed: pip install paho-mqtt") from exc
        host, _, port = cfg.url.removeprefix("mqtt://").partition(":")
        self._client = mqtt.Client()
        self._client.connect(host or "localhost", int(port or 1883))
        self._client.loop_start()

    async def _apply(self, device: SmartDevice, state: dict[str, Any]) -> None:  # pragma: no cover
        self._client.publish(f"cyberdyne/{device.id}/set", json.dumps(state))


class HassHub(DeviceHub):
    """Home Assistant REST: ``POST /api/services/<domain>/turn_on|turn_off``."""

    def __init__(self, cfg: HomeConfig) -> None:
        super().__init__(cfg)
        self.url = cfg.url.rstrip("/")
        self.token = cfg.token or os.environ.get("HASS_TOKEN", "")

    async def _apply(self, device: SmartDevice, state: dict[str, Any]) -> None:  # pragma: no cover
        domain = {"light": "light", "fan": "fan", "plug": "switch", "door": "cover"}.get(device.kind, "switch")
        service = "turn_on" if state.get("on") else "turn_off"
        body = json.dumps({"entity_id": f"{domain}.{device.id}"}).encode()
        req = urllib.request.Request(f"{self.url}/api/services/{domain}/{service}", data=body,
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()


def build_hub(cfg: HomeConfig) -> DeviceHub:
    return {"virtual": VirtualHub, "mqtt": MQTTHub, "hass": HassHub}[cfg.hub](cfg)
