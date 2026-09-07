"""Dashboard: stdlib HTTP server in a thread, serving a single-page control room.

    GET  /            HTML
    GET  /api/state   telemetry snapshot (JSON)
    GET  /metrics     Prometheus text format
    POST /api/say     {"text": "..."}  -> language/utterance
    POST /api/estop   {"engage": bool} -> safety/estop
    POST /api/goal    {"x":..,"y":..}  -> nav/goal
    POST /api/plan    {"goal": "..."}  -> brain/goal   (council deliberates)
    POST /api/answer  {"answer": "..."} -> human/answer
"""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..kernel.context import Context
from ..kernel.module import Module

_HTML = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")


def prometheus_metrics(ctx) -> str:
    """Prometheus text exposition of the robot's vital signs (scrape /metrics)."""
    bus = ctx.bus
    name = ctx.config.name
    lines = []

    def g(metric: str, value, help_: str, labels: str = "") -> None:
        lines.append(f"# HELP cyberdyne_{metric} {help_}\n# TYPE cyberdyne_{metric} gauge")
        lab = f'robot="{name}"' + (f",{labels}" if labels else "")
        lines.append(f"cyberdyne_{metric}{{{lab}}} {value}")
    batt = bus.latest_payload("sensor/battery") or {}
    g("battery_level", batt.get("level", 0.0), "battery state of charge 0..1")
    g("estop_engaged", int(ctx.safety.estop.engaged), "1 when the e-stop is engaged")
    g("bus_messages_total", bus.stats.published, "messages published on the bus")
    g("bus_handler_errors_total", bus.stats.handler_errors, "subscriber exceptions")
    g("audit_entries", len(ctx.safety.audit), "audit log length")
    g("sim_time_seconds", ctx.now, "kernel clock")
    states = {s: 0 for s in ("boot", "diagnostic", "idle", "active", "charging", "estop", "shutdown")}
    states[ctx.state.state.value] = 1
    for st, v in states.items():
        g("state", v, "system state (one-hot)", f'state="{st}"')
    tel = ctx.extras.get("telemetry")
    sched = tel._scheduler if tel else None
    for m in (sched.modules if sched else []):
        lab = f'module="{m.name}"'
        g("module_ticks_total", m.stats.ticks, "ticks per module", lab)
        g("module_faults_total", m.stats.faults, "faults per module", lab)
        g("module_restarts_total", m.stats.restarts, "restarts per module", lab)
        g("module_tick_seconds", round(m.stats.last_tick_duration, 6), "last tick duration", lab)
        g("module_rate_hz", m.rate_hz, "current rate", lab)
    tasks = ctx.extras.get("tasks")
    if tasks:
        g("tasks_completed_total", tasks.completed, "tasks done")
        g("tasks_failed_total", tasks.failed, "tasks failed")
    return "\n".join(lines) + "\n"


class Dashboard(Module):
    name = "dashboard"
    rate_hz = 1.0
    priority = 95

    def __init__(self, host: str = "127.0.0.1", port: int = 8080) -> None:
        super().__init__()
        self.host, self.port = host, port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.requests = 0

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        loop = asyncio.get_running_loop()
        module = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:  # silence
                pass

            def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                module.requests += 1
                if self.path == "/":
                    self._send(200, _HTML.encode(), "text/html; charset=utf-8")
                elif self.path == "/api/state":
                    tel = module.ctx.extras.get("telemetry")
                    snap = tel.snapshot if tel and tel.snapshot else (tel.build() if tel else {})
                    self._send(200, json.dumps(snap, default=str).encode())
                elif self.path == "/metrics":
                    self._send(200, prometheus_metrics(module.ctx).encode(), "text/plain; version=0.0.4")
                else:
                    self._send(404, b'{"error":"not found"}')

            def do_POST(self) -> None:  # noqa: N802
                module.requests += 1
                n = int(self.headers.get("Content-Length", 0))
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except json.JSONDecodeError:
                    self._send(400, b'{"error":"bad json"}')
                    return
                bus = module.ctx.bus
                if self.path == "/api/say":
                    bus.publish_threadsafe(loop, "language/utterance", {"text": body.get("text", ""),
                                                                        "speaker": "dashboard",
                                                                        "confirmed": bool(body.get("confirmed"))})
                elif self.path == "/api/estop":
                    bus.publish_threadsafe(loop, "safety/estop", {"engage": bool(body.get("engage", True)),
                                                                  "reason": "dashboard",
                                                                  "confirmed": True})
                elif self.path == "/api/answer":
                    bus.publish_threadsafe(loop, "human/answer", {"answer": str(body.get("answer", "")),
                                                                  "id": body.get("id")})
                elif self.path == "/api/plan":
                    bus.publish_threadsafe(loop, "brain/goal", {"goal": str(body.get("goal", ""))})
                elif self.path == "/api/goal":
                    bus.publish_threadsafe(loop, "nav/goal", {"x": float(body["x"]), "y": float(body["y"]),
                                                              "name": "dashboard"})
                else:
                    self._send(404, b'{"error":"not found"}')
                    return
                self._send(200, b'{"ok":true}')

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="dashboard")
        self._thread.start()
        self.log.info("dashboard on http://%s:%s", self.host, self.port)

    async def teardown(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    async def tick(self, dt: float) -> None:
        pass

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"
