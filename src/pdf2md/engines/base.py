"""The engine seam. Everything downstream sees pdf2md types only; this is the
one boundary where a concrete conversion engine (Docling today, MinerU/PaddleOCR
later) is allowed to exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol, runtime_checkable

import pypdfium2 as pdfium

from pdf2md.schema import BBox, Block, FigureRef, RawTable, TableData


@dataclass
class EngineResult:
    """Engine-neutral output: blocks in reading order plus the table/figure
    detail and per-page geometry the downstream stages need."""

    blocks: list[Block]
    tables: list[TableData]
    figures: list[FigureRef]
    page_sizes: dict[int, tuple[float, float]]  # page_no -> (width, height) in pts
    engine_versions: dict[str, str] = field(default_factory=dict)
    # Native engine quality evidence. Scores remain engine-specific observations,
    # not calibrated probabilities; downstream surfaces the corresponding grades.
    quality_evidence: dict[str, object] = field(default_factory=dict)
    # Structured cells per table (block_id -> RawTable), so `enrich` can rebuild a
    # table with recovered scripts. Transient: lives here, never serialized.
    raw_tables: dict[str, RawTable] = field(default_factory=dict)


@runtime_checkable
class Engine(Protocol):
    name: str

    def convert(self, pdf_path: Path) -> EngineResult: ...


def normalize_page_origin(result: EngineResult, pdf_path: Path) -> None:
    """Shift every bbox in an engine's output into PDF user space, in place.

    Engines report coordinates relative to the page's visible (crop) area,
    whose corner most PDFs put at (0, 0) — but not all: an ACS paper measured
    here carries a MediaBox origin of (9, 9), an Elsevier one a CropBox origin
    of (20, 62). pdfium's coordinates (glyph charboxes, `set_cropbox`, page
    object bounds) are absolute user space, so on those documents every glyph
    check read ink 9pt (or 62pt) away from the text it was scoring: mean word
    recall 0.53 and 0.21, both above 0.94 once shifted by exactly the origin.

    Canonicalizing on user space at this seam is the one-touchpoint fix; the
    only consumer living in visible-area space is the raster crop mapping in
    `render.py`, which subtracts the same origin. A (0, 0)-origin document —
    14 of the 17 measured — passes through untouched.
    """
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        origins: dict[int, tuple[float, float]] = {}
        for page_no in result.page_sizes:
            box = pdf[page_no - 1].get_bbox()  # (left, bottom, right, top)
            if abs(box[0]) > 1e-6 or abs(box[1]) > 1e-6:
                origins[page_no] = (box[0], box[1])
    finally:
        pdf.close()
    if not origins:
        return

    def shift(bbox: BBox | None, page: int) -> BBox | None:
        origin = origins.get(page)
        if bbox is None or origin is None:
            return bbox
        dx, dy = origin
        return BBox(x0=bbox.x0 + dx, y0=bbox.y0 + dy, x1=bbox.x1 + dx, y1=bbox.y1 + dy)

    for b in result.blocks:
        b.bbox = shift(b.bbox, b.page)
    for t in result.tables:
        t.bbox = shift(t.bbox, t.page)
        raw = result.raw_tables.get(t.block_id)
        if raw is not None:
            raw.cells = [replace(c, bbox=shift(c.bbox, t.page)) for c in raw.cells]
    for f in result.figures:
        f.bbox = shift(f.bbox, f.page)
        f.caption_bbox = shift(f.caption_bbox, f.page)
