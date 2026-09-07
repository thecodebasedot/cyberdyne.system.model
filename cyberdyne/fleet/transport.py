from __future__ import annotations

import json
import socket

from .interfaces import FleetMessage, Transport


class InMemoryTransport(Transport):
    """Shared mailbox for robots living in one process (tests, FleetSim)."""

    class Hub:
        def __init__(self) -> None:
            self.queues: dict[str, list[FleetMessage]] = {}
            self.sent = 0

        def attach(self, robot: str) -> None:
            self.queues.setdefault(robot, [])

        def broadcast(self, msg: FleetMessage) -> None:
            self.sent += 1
            for robot, q in self.queues.items():
                if robot != msg.robot:
                    q.append(msg)

    def __init__(self, hub: InMemoryTransport.Hub, robot: str) -> None:
        self.hub, self.robot = hub, robot
        hub.attach(robot)

    async def send(self, msg: FleetMessage) -> None:
        self.hub.broadcast(msg)

    async def receive(self) -> list[FleetMessage]:
        out, self.hub.queues[self.robot] = self.hub.queues[self.robot], []
        return out


class UDPTransport(Transport):
    """LAN broadcast over UDP, JSON per datagram. No dependencies."""

    def __init__(self, robot: str, port: int = 47001, bind: str = "0.0.0.0", target: str = "255.255.255.255") -> None:
        self.robot, self.port, self.target = robot, port, target
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.bind((bind, port))
        self._sock.setblocking(False)

    async def send(self, msg: FleetMessage) -> None:
        self._sock.sendto(json.dumps(msg.__dict__, default=str).encode(), (self.target, self.port))

    async def receive(self) -> list[FleetMessage]:
        out = []
        while True:
            try:
                data, _ = self._sock.recvfrom(65535)
            except BlockingIOError:
                break
            try:
                d = json.loads(data)
                if d.get("robot") != self.robot:
                    out.append(FleetMessage(d["robot"], d["topic"], d.get("payload"), float(d.get("ts", 0))))
            except (ValueError, KeyError):
                continue
        return out

    def close(self) -> None:
        self._sock.close()
