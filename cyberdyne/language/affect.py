"""Affect: a small sentiment lexicon (English + romanised Bangla) -> mood in [-1, 1]."""
from __future__ import annotations

import re

POSITIVE = {"thanks", "thank", "great", "good", "nice", "love", "awesome", "perfect", "happy", "well done",
            "dhonnobad", "bhalo", "khub bhalo", "darun", "shabash", "moja", "khushi", "sundor"}
NEGATIVE = {"bad", "stupid", "useless", "angry", "hate", "wrong", "terrible", "slow", "annoying", "stop it",
            "kharap", "bokka", "faltu", "birokto", "rag", "bhul", "dhet", "chup"}
INTENSIFIERS = {"very", "so", "khub", "onek", "ekdom", "!"}


def mood(text: str) -> tuple[float, list[str]]:
    t = text.lower()
    words = set(re.findall(r"[a-z]+", t))
    hits = []
    score = 0.0
    for w in POSITIVE:
        if w in t and (" " in w or w in words):
            score += 1.0
            hits.append(w)
    for w in NEGATIVE:
        if w in t and (" " in w or w in words):
            score -= 1.0
            hits.append(w)
    if not hits:
        return 0.0, []
    boost = 1.0 + 0.5 * sum(1 for i in INTENSIFIERS if i in t)
    return max(-1.0, min(1.0, score * boost / max(1, len(hits)))), hits
