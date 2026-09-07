"""Personality: consistent phrasing on top of whatever the robot has to say.

The content of speech comes from skills and the brain; personality only
changes the wrapping (address, fillers, length) so behaviour stays testable
and the same robot sounds like the same robot every day.
"""
from __future__ import annotations

from ..kernel.config import PersonalityConfig


class Personality:
    def __init__(self, cfg: PersonalityConfig) -> None:
        self.cfg = cfg

    def address(self, speaker: str | None) -> str:
        if not speaker:
            return ""
        if self.cfg.formality >= 0.7:
            return f"{speaker} sahib, " if self.cfg.language == "bn" else f"{speaker}, "
        if self.cfg.warmth >= 0.7:
            return f"{speaker} bhai, " if self.cfg.language == "bn" else f"Hey {speaker}, "
        return ""

    def wrap(self, text: str, speaker: str | None = None, voice: str = "neutral") -> str:
        if voice == "alert":
            return text                                  # alerts are never softened
        prefix = self.address(speaker)
        filler = ""
        if self.cfg.verbosity >= 0.7:
            filler = "Ji, " if self.cfg.language == "bn" else "Sure. "
        if self.cfg.verbosity <= 0.2 and len(text) > 80:
            text = text.split(". ")[0].rstrip(".") + "."
        out = f"{prefix}{filler}{text}".strip()
        if self.cfg.warmth >= 0.8 and voice == "friendly" and not out.endswith("!"):
            out = out.rstrip(".") + "!"
        if (prefix or filler) and out:
            out = out[0].upper() + out[1:]
        return out
