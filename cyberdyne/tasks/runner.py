"""TaskRunner: executes one task at a time, one step at a time.

    in : task/start   {name, steps?, origin?, trust?}   (steps omitted -> library lookup)
         task/pause, task/resume, task/cancel
         nav/arrived, nav/status, skill/result
    out: task/status  (every tick, latest task)         task/step {id, idx, kind}
         task/done, task/failed, task/cancelled, task/paused, task/resumed

Pause cancels the current navigation goal and remembers the step; resume
re-dispatches it. Steps time out and retry; a step that runs out of retries
fails the task. Sub-tasks run in place by splicing their steps in.
"""
from __future__ import annotations

from ..kernel.context import Context
from ..kernel.module import Module
from .model import Task, TaskStatus, TaskStep


class TaskRunner(Module):
    name = "tasks"
    rate_hz = 10.0
    priority = 46            # after skills (45), before brain (50)

    def __init__(self) -> None:
        super().__init__()
        self.library: dict[str, list[TaskStep]] = {}
        self.task: Task | None = None
        self.queue: list[Task] = []
        self.history: list[Task] = []
        self.completed = self.failed = 0

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        for t in ctx.config.tasks:
            self.define(t["name"], [TaskStep.from_dict(s) for s in t["steps"]])
        self._subs = [ctx.bus.subscribe(topic, fn, name=f"tasks.{topic}") for topic, fn in (
            ("task/start", self._on_start), ("task/pause", self._on_pause), ("task/resume", self._on_resume),
            ("task/cancel", self._on_cancel), ("nav/arrived", self._on_arrived),
            ("skill/result", self._on_result))]
        ctx.extras["tasks"] = self

    async def teardown(self) -> None:
        for s in self._subs:
            self.ctx.bus.unsubscribe(s)

    # -- library ------------------------------------------------------------
    def define(self, name: str, steps: list[TaskStep]) -> None:
        self.library[name] = steps

    def describe_library(self) -> dict[str, list[dict]]:
        return {k: [s.to_dict() for s in v] for k, v in self.library.items()}

    # -- events -------------------------------------------------------------
    async def _on_start(self, msg) -> None:
        p = msg.payload or {}
        name = str(p.get("name", "task"))
        raw = p.get("steps")
        if raw is None:
            if name not in self.library:
                await self.ctx.bus.publish("task/failed", {"name": name, "error": "unknown task"}, source=self.name)
                return
            steps = list(self.library[name])
        else:
            steps = [TaskStep.from_dict(s) for s in raw]
        task = Task(name, steps, origin=str(p.get("origin", msg.source)), trust=str(p.get("trust", "owner")))
        if self.task and self.task.active and p.get("queue"):
            self.queue.append(task)
            return
        if self.task and self.task.active:
            await self._finish(self.task, TaskStatus.CANCELLED, "superseded")
        await self._begin(task)

    async def _begin(self, task: Task) -> None:
        self.task = task
        task.status, task.started = TaskStatus.RUNNING, self.ctx.now
        self.ctx.safety.audit.record(self.ctx.now, "tasks", "start", task=task.id, name=task.name,
                                     steps=len(task.steps), origin=task.origin)
        await self.ctx.bus.publish("task/started", task.to_dict(), source=self.name)

    async def _on_pause(self, msg) -> None:
        t = self.task
        if t and t.status == TaskStatus.RUNNING:
            t.status, t.in_flight = TaskStatus.PAUSED, False
            await self.ctx.bus.publish("nav/cancel", None, source=self.name)
            await self.ctx.bus.publish("task/paused", {**t.to_dict(), "reason": (msg.payload or {}).get("reason")},
                                       source=self.name)

    async def _on_resume(self, msg) -> None:
        t = self.task
        if t and t.status == TaskStatus.PAUSED:
            t.status = TaskStatus.RUNNING
            await self.ctx.bus.publish("task/resumed", t.to_dict(), source=self.name)

    async def _on_cancel(self, msg) -> None:
        if self.task and self.task.active:
            await self.ctx.bus.publish("nav/cancel", None, source=self.name)
            await self._finish(self.task, TaskStatus.CANCELLED, str((msg.payload or {}).get("reason", "cancelled")))

    async def _on_arrived(self, msg) -> None:
        t = self.task
        if t and t.status == TaskStatus.RUNNING and t.in_flight and t.current and t.current.kind == "goto":
            await self._step_done({"arrived": msg.payload})

    async def _on_result(self, msg) -> None:
        t = self.task
        p = msg.payload or {}
        if t and t.status == TaskStatus.RUNNING and t.in_flight and p.get("request_id") == f"task:{t.id}:{t.idx}":
            if p.get("ok"):
                await self._step_done(p)
            else:
                await self._step_failed(str(p.get("error", "skill failed")))

    # -- stepping -----------------------------------------------------------------
    async def _dispatch(self, t: Task) -> None:
        step = t.current
        t.in_flight, t.step_started = True, self.ctx.now
        bus = self.ctx.bus
        await bus.publish("task/step", {"id": t.id, "idx": t.idx, "kind": step.kind, "args": step.args,
                                        "attempt": t.attempts + 1}, source=self.name)
        if step.kind == "goto":
            await bus.publish("nav/goal", {"x": float(step.args["x"]), "y": float(step.args["y"]),
                                           "name": step.args.get("name", t.name)}, source=self.name)
        elif step.kind == "skill":
            await bus.publish("skill/invoke", {"skill": step.args["skill"], "args": step.args.get("args", {}),
                                               "request_id": f"task:{t.id}:{t.idx}", "trust": t.trust,
                                               "confirmed": bool(step.args.get("confirmed"))}, source=self.name)
        elif step.kind == "wait":
            pass                                             # tick() completes it when time is up
        elif step.kind == "task":
            sub = self.library.get(str(step.args.get("name")))
            if sub is None:
                await self._step_failed(f"unknown sub-task {step.args.get('name')}")
                return
            t.steps[t.idx:t.idx + 1] = list(sub)              # splice: composition without recursion
            t.in_flight = False
        else:
            await self._step_failed(f"unknown step kind {step.kind}")

    async def _step_done(self, result: dict) -> None:
        t = self.task
        t.results.append({"idx": t.idx, "kind": t.current.kind, "result": result})
        t.idx, t.attempts, t.in_flight = t.idx + 1, 0, False
        if t.idx >= len(t.steps):
            await self._finish(t, TaskStatus.DONE)

    async def _step_failed(self, error: str) -> None:
        t = self.task
        t.attempts += 1
        t.in_flight = False
        if t.attempts > t.current.retries:
            await self._finish(t, TaskStatus.FAILED, f"step {t.idx} ({t.current.kind}): {error}")
        else:
            await self.ctx.bus.publish("task/retry", {"id": t.id, "idx": t.idx, "attempt": t.attempts,
                                                      "error": error}, source=self.name)

    async def _finish(self, t: Task, status: TaskStatus, error: str = "") -> None:
        t.status, t.finished, t.error, t.in_flight = status, self.ctx.now, error, False
        self.history.append(t)
        self.history = self.history[-50:]
        if status == TaskStatus.DONE:
            self.completed += 1
        elif status == TaskStatus.FAILED:
            self.failed += 1
        self.ctx.safety.audit.record(self.ctx.now, "tasks", status.value, task=t.id, name=t.name, error=error)
        await self.ctx.bus.publish(f"task/{status.value}", t.to_dict(), source=self.name)
        if self.task is t:
            self.task = None
        if self.queue:
            await self._begin(self.queue.pop(0))

    async def tick(self, dt: float) -> None:
        t = self.task
        if t and t.status == TaskStatus.RUNNING:
            step = t.current
            if step is None:
                await self._finish(t, TaskStatus.DONE)
            elif not t.in_flight:
                await self._dispatch(t)
            elif step.kind == "wait":
                if self.ctx.now - t.step_started >= float(step.args.get("seconds", 0)):
                    await self._step_done({"waited": step.args.get("seconds")})
            elif self.ctx.now - t.step_started > step.timeout:
                await self._step_failed(f"timed out after {step.timeout}s")
        await self.ctx.bus.publish("task/status", {"active": bool(self.task and self.task.active),
                                                   "task": self.task.to_dict() if self.task else None,
                                                   "queued": len(self.queue), "completed": self.completed,
                                                   "failed": self.failed}, source=self.name)
