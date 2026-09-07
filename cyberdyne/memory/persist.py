"""Store: JSON persistence for everything the robot learns across runs.

    <data_dir>/facts.json      knowledge graph
    <data_dir>/episodes.json   episodic memory (most recent 2000)
    <data_dir>/user_model.json habits
    <data_dir>/tasks.json      taught routes / task library additions
    <data_dir>/macros.json     self-authored macro skills
"""
from __future__ import annotations

import json
from pathlib import Path


class Store:
    def __init__(self, data_dir: str | Path) -> None:
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.writes = 0

    def _path(self, name: str) -> Path:
        return self.dir / f"{name}.json"

    def load(self, name: str, default):
        p = self._path(name)
        if not p.exists():
            return default
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default

    def save(self, name: str, data) -> None:
        self.writes += 1
        tmp = self._path(name).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        tmp.replace(self._path(name))

    # -- typed helpers ---------------------------------------------------------
    def save_task(self, name: str, steps: list[dict]) -> None:
        tasks = self.load("tasks", {})
        tasks[name] = steps
        self.save("tasks", tasks)

    def save_macro(self, macro: dict) -> None:
        macros = self.load("macros", {})
        macros[macro["name"]] = macro
        self.save("macros", macros)

    def snapshot(self, memory, user_model, tasks_lib: dict | None = None) -> None:
        self.save("facts", memory.semantic.all())
        self.save("episodes", [e.to_dict() for e in memory.episodic.recent(2000)])
        if user_model is not None:
            self.save("user_model", user_model.to_dict())
        if tasks_lib:
            self.save("tasks", tasks_lib)
