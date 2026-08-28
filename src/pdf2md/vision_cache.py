"""Persist document-scoped vision inference results between conversion runs.

Cache identities are built by the calling inference adapter. This module owns
only the shared on-disk file used by scan OCR and visual enrichment stages.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


_CACHE_NAME = "describe_cache.json"


@dataclass
class CacheStats:
    lookups: int = 0
    hits: int = 0
    writes: int = 0

    def snapshot(self) -> dict[str, int]:
        return {
            "lookups": self.lookups,
            "hits": self.hits,
            "misses": self.lookups - self.hits,
            "writes": self.writes,
        }

    def since(self, earlier: dict[str, int]) -> dict[str, int]:
        current = self.snapshot()
        return {name: current[name] - earlier[name] for name in current}


class VisionCache(dict):
    def __init__(
        self,
        values: dict,
        stats: CacheStats | None = None,
        path: Path | None = None,
    ) -> None:
        super().__init__(values)
        self.stats = stats
        self.path = path

    def get(self, key, default=None):
        if self.stats is not None:
            self.stats.lookups += 1
            if key in self:
                self.stats.hits += 1
        return super().get(key, default)

    def __setitem__(self, key, value) -> None:
        if self.stats is not None:
            self.stats.writes += 1
        super().__setitem__(key, value)
        if self.path is not None:
            _write_cache(self.path, self)


def load_vision_cache(document_dir: Path, stats: CacheStats | None = None) -> VisionCache:
    path = document_dir / _CACHE_NAME
    values = json.loads(path.read_text()) if path.exists() else {}
    return VisionCache(values, stats, path)


def write_vision_cache(document_dir: Path, cache: dict) -> None:
    document_dir.mkdir(parents=True, exist_ok=True)
    _write_cache(document_dir / _CACHE_NAME, cache)


def _write_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".json.tmp")
    pending.write_text(json.dumps(cache, indent=2))
    pending.replace(path)
