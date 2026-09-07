"""Dashboard: stdlib HTTP server in a thread, serving a single-page control room.

    GET  /            HTML
    GET  /api/state   telemetry snapshot (JSON)
    POST /api/say     {"text": "..."}  -> language/utterance
    POST /api/estop   {"engage": bool} -> safety/estop
    POST /api/goal    {"x":..,"y":..}  -> nav/goal
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
