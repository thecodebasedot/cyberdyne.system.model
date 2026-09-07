from __future__ import annotations

import importlib
import inspect

from .base import Skill


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> Skill:
        self._skills[skill.manifest.name] = skill
        return skill

    def load_module(self, dotted: str) -> list[Skill]:
        """Import a module and register every Skill subclass found in it."""
        mod = importlib.import_module(dotted)
        found = []
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if issubclass(obj, Skill) and obj is not Skill and hasattr(obj, "manifest"):
                found.append(self.register(obj()))
        return found

    def load_builtin(self) -> list[Skill]:
        found = self.load_module("cyberdyne.skills.builtin.core")
        found += self.load_module("cyberdyne.skills.builtin.home")
        found += self.load_module("cyberdyne.skills.authoring")
        return found

    def get(self, name: str) -> Skill:
        try:
            return self._skills[name]
        except KeyError:
            raise KeyError(f"unknown skill: {name}") from None

    def names(self) -> list[str]:
        return sorted(self._skills)

    def describe(self) -> list[dict]:
        return [s.manifest.to_dict() for s in self._skills.values()]
