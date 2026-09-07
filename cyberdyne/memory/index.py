"""Text similarity index with a pluggable embedder.

``HashEmbedder`` is dependency-free: hashed bag-of-words with character
trigrams, L2-normalised, cosine similarity. It is deliberately simple; a
sentence-transformer or API embedder implements the same ``embed`` and the
index does not change.
"""
from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod


class Embedder(ABC):
    dim: int

    @abstractmethod
    def embed(self, text: str) -> list[float]: ...


class HashEmbedder(Embedder):
    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    @staticmethod
    def _tokens(text: str) -> list[str]:
        words = re.findall(r"[a-z0-9]+", text.lower())
        grams = [w[i:i + 3] for w in words for i in range(max(1, len(w) - 2))]
        return words + grams

    def embed(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in self._tokens(text):
            h = int(hashlib.blake2b(tok.encode(), digest_size=4).hexdigest(), 16)
            v[h % self.dim] += 1.0 if (h >> 31) else -1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]


class TextIndex:
    def __init__(self, embedder: Embedder | None = None) -> None:
        self.embedder = embedder or HashEmbedder()
        self._items: list[tuple[int, list[float]]] = []

    def add(self, key: int, text: str) -> None:
        self._items.append((key, self.embedder.embed(text)))

    def remove(self, keys: set[int]) -> None:
        self._items = [(k, v) for k, v in self._items if k not in keys]

    def search(self, text: str, limit: int = 5) -> list[tuple[int, float]]:
        q = self.embedder.embed(text)
        scored = [(k, sum(a * b for a, b in zip(q, v, strict=True))) for k, v in self._items]
        return sorted(scored, key=lambda kv: kv[1], reverse=True)[:limit]

    def __len__(self) -> int:
        return len(self._items)
