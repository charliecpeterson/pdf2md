"""The engine seam. Everything downstream sees pdf2md types only; this is the
one boundary where a concrete conversion engine (Docling today, MinerU/PaddleOCR
later) is allowed to exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from pdf2md.schema import Block, FigureRef, TableData


@dataclass
class EngineResult:
    """Engine-neutral output: blocks in reading order plus the table/figure
    detail and per-page geometry the downstream stages need."""

    blocks: list[Block]
    tables: list[TableData]
    figures: list[FigureRef]
    page_sizes: dict[int, tuple[float, float]]  # page_no -> (width, height) in pts
    engine_versions: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class Engine(Protocol):
    name: str

    def convert(self, pdf_path: Path) -> EngineResult: ...
