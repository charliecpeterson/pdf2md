"""Rebuild a born-digital table's grid from glyph geometry alone.

The engine's table structure is a model output; on a born-digital page the glyph
coordinates are measurement. Within one table region, every true column
separator is a whitespace corridor no glyph crosses, so grouping chars into
visual lines (`scripts._lines`) and splitting lanes on tall whitespace gaps
recovers the grid deterministically — the same trust model as digitize.py's
vector-path charts, applied to tables.

Conservative like row_locator.py: it refuses instead of guessing when no column
structure emerges, and its output is diff/verification evidence, never an
automatic replacement for engine cells.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from pdf2md.schema import BBox, RawTable
from pdf2md.scripts import Char, PageChars, _lines

# Occupancy bin width for the whitespace projection (PDF points).
_BIN = 0.5
# A lane separator is any whitespace corridor no glyph crosses in any row,
# at least this wide (keeps sub-point kerning noise out). Width is not the
# criterion -- zero-crossing is: intra-cell word gaps only survive when no
# other row's ink crosses them, which is exactly the column-aligned case.
_MIN_SEP_PT = 1.0


@dataclass
class RebuiltGrid:
    """A table region rebuilt from glyphs: `rows` top-down (each a list of cell
    texts per lane, '' when empty), plus the geometry that produced it."""

    rows: list[list[str]]
    lane_bounds: list[tuple[float, float]]  # x-intervals, left to right
    line_bands: list[tuple[float, float]]   # per row: (y_low, y_high), PDF y-up
    evidence: dict[str, Any] = field(default_factory=dict)


def rebuild_grid(chars: list[Char]) -> tuple[RebuiltGrid | None, dict[str, Any], str | None]:
    """Cluster region chars into (rows x lanes). Returns (grid, evidence, refusal);
    grid is None iff refusal is set. Deterministic, model-free."""
    evidence: dict[str, Any] = {"chars": len(chars)}
    ink = [c for c in chars if c[0].strip()]
    if len(ink) < 4:
        return None, evidence, "region_has_no_text"

    widths = [c[3] - c[1] for c in ink]
    evidence["median_char_width"] = round(statistics.median(widths), 2)

    # Whitespace projection over x: bins covered by any glyph are ink.
    lo = min(c[1] for c in ink)
    hi = max(c[3] for c in ink)
    n_bins = int((hi - lo) / _BIN) + 1
    covered = [False] * n_bins
    for c in ink:
        first = max(0, int((c[1] - lo) / _BIN))
        last = min(n_bins - 1, int((c[3] - lo) / _BIN))
        for b in range(first, last + 1):
            covered[b] = True

    # Zero-crossing runs >= _MIN_SEP_PT are lane separators.
    separators: list[tuple[float, float]] = []
    run_start = None
    for i, is_ink in enumerate(covered + [True]):
        if not is_ink and run_start is None:
            run_start = i
        elif is_ink and run_start is not None:
            if (i - run_start) * _BIN >= _MIN_SEP_PT:
                separators.append((lo + run_start * _BIN, lo + i * _BIN))
            run_start = None
    evidence["separators"] = [[round(a, 1), round(b, 1)] for a, b in separators]
    if len(separators) < 1:
        return None, evidence, "column_structure_unavailable"

    lane_bounds: list[tuple[float, float]] = []
    left = lo - 1.0
    for gap_lo, gap_hi in separators:
        lane_bounds.append((left, gap_lo))
        left = gap_hi
    lane_bounds.append((left, hi + 1.0))

    # Visual lines become rows, top-down (PDF y-up). Each char joins the lane its
    # center falls in; by construction no glyph crosses a separator corridor.
    # Whitespace glyphs ride along: they're excluded from lane geometry above but
    # are real cell content ("Training Cost").
    lines: list[list[Char]] = []
    for group in _lines(chars):
        if any(c[0].strip() for c in group):
            lines.append(group)
    lines.sort(key=lambda g: -statistics.median((c[2] + c[4]) / 2 for c in g if c[0].strip()))

    def lane_of(x: float) -> int:
        for i, (lane_lo, lane_hi) in enumerate(lane_bounds):
            if lane_lo <= x <= lane_hi:
                return i
        return 0  # unreachable: lanes span [lo-1, hi+1]

    rows: list[list[str]] = []
    bands: list[tuple[float, float]] = []
    for group in lines:
        cells = [""] * len(lane_bounds)
        for c in sorted(group, key=lambda c: c[1]):
            cells[lane_of((c[1] + c[3]) / 2)] += c[0]
        rows.append([" ".join(cell.split()) for cell in cells])
        bands.append((min(c[2] for c in group), max(c[4] for c in group)))

    evidence.update({"lanes": len(lane_bounds), "rows": len(rows)})
    return RebuiltGrid(rows=rows, lane_bounds=lane_bounds, line_bands=bands,
                       evidence=evidence), evidence, None


def locate(grid: RebuiltGrid, x: float, y: float) -> tuple[int, int] | None:
    """(row, lane) whose band contains the point, or None when outside the grid.
    Rows are vertically disjoint by construction; ties break to the nearest band."""
    row_hits = [
        i for i, (lo, hi) in enumerate(grid.line_bands) if lo - 1.0 <= y <= hi + 1.0
    ]
    if not row_hits:
        return None
    row = min(
        row_hits,
        key=lambda i: abs(y - sum(grid.line_bands[i]) / 2),
    )
    lane = next(
        (i for i, (lo, hi) in enumerate(grid.lane_bounds) if lo <= x <= hi), None
    )
    if lane is None:
        return None
    return row, lane


# ---------------------------------------------------------------------------
# Per-engine-cell verification: the engine provides structure, glyphs provide
# truth. Each engine cell's bbox defines where its content must be; the glyph
# layer inside that box is what the PDF actually shows there. Read-only
# evidence for profile.json — a mismatch flags review, it never rewrites.

_SCRIPT_TAGS = re.compile(r"</?(?:sub|sup)>")
_MINUS_MAP = str.maketrans({"−": "-", "\u2011": "-", "\u2013": "-"})
# In-number spacing conventions ("- 2846.292", "2 . 3") are typesetting, not
# content; close them so both sides compare as the value they spell.
_INNER_NUM_GAP = re.compile(r"(?<=[\d.,%-]) (?=[\d.,%-])|(?<=[\d.,%-]) (?=[\d.%])")
_CELL_PAD_PT = 2.0   # cell boxes hug cap height; ascenders and dots need slack
_STRAY_WINDOW_PT = 6.0  # how far past the outer cells dropped-column ink may sit
_READ_INSET_PT = 0.5  # read tighter than the box: neighbour ink bleeds through pads


def _read_box(bbox) -> BBox:
    """Slightly inset box for *reading* a cell's glyphs. Containment keeps the
    padded box; reading must not sweep a dense neighbour's edge characters in."""
    return BBox(
        x0=min(bbox.x0, bbox.x1) + _READ_INSET_PT,
        y0=min(bbox.y0, bbox.y1) + _READ_INSET_PT,
        x1=max(bbox.x0, bbox.x1) - _READ_INSET_PT,
        y1=max(bbox.y0, bbox.y1) - _READ_INSET_PT,
    )


def content_norm(text: str | None) -> str:
    """Normalize engine/grid/glyph cell readings to comparable content: script
    tags and entities stripped, NFKC (ligatures, superscript digits), dashes
    unified, in-number gaps closed, whitespace collapsed."""
    if not text:
        return ""
    text = _SCRIPT_TAGS.sub("", text)
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">")):
        text = text.replace(entity, char)
    text = unicodedata.normalize("NFKC", text).translate(_MINUS_MAP)
    text = _INNER_NUM_GAP.sub("", text)
    return " ".join(text.split()).strip()


def _reading(chars: list[Char]) -> str:
    """Glyphs as read: visual lines top-down (the same overlap grouping used
    everywhere scripts geometry is read), left-to-right within a line. Word gaps
    become spaces -- PDFs don't draw space glyphs, but engine text has them.
    Sorting by box top alone scrambles lines: dotted 'i's and descenders shift
    tops."""
    parts: list[str] = []
    for line in _lines(chars):
        line = [c for c in line if c[0].strip()]
        if not line:
            continue
        line.sort(key=lambda c: c[1])
        height = statistics.median(c[4] - c[2] for c in line)
        buf: list[str] = []
        prev = None
        for c in line:
            if prev is not None and c[1] - prev[3] > 0.22 * height:
                buf.append(" ")
            buf.append(c[0])
            prev = c
        parts.append("".join(buf))
    return " ".join(parts)


def glyph_unbacked_tables(tables) -> set[str]:
    """Block ids of tables whose engine cells come from pixels, not the page's
    text layer: most text-bearing cells verified `engine_without_glyphs`. These
    are raster tables a vision model read (TableFormer over an embedded image);
    like scanned tables their crop must ride along as authoritative and their
    cells stay candidates. A stray unbacked cell among exact ones never trips
    this — the fraction has to be majority."""
    out = set()
    for t in tables:
        cells = (t.cell_glyph_check or {}).get("cells", {})
        ewog = cells.get("engine_without_glyphs", 0)
        if not ewog:
            continue
        textual = sum(
            cells.get(k, 0)
            for k in ("exact", "spacing_only", "mismatch", "engine_without_glyphs")
        )
        if textual and ewog * 2 >= textual:
            out.add(t.block_id)
    return out


def _squash(text: str) -> str:
    """The content with all whitespace removed: the last-word comparison tier."""
    return re.sub(r"\s+", "", text)


def _pad_box(bbox) -> BBox:
    """Expand a box by the containment pad, normalizing the Docling y0>y1
    orientation so plain comparisons hold."""
    return BBox(
        x0=min(bbox.x0, bbox.x1) - _CELL_PAD_PT,
        y0=min(bbox.y0, bbox.y1) - _CELL_PAD_PT,
        x1=max(bbox.x0, bbox.x1) + _CELL_PAD_PT,
        y1=max(bbox.y0, bbox.y1) + _CELL_PAD_PT,
    )


def check_table_cells(raw_table: RawTable, pc: PageChars, *,
                      per_cell: bool = False,
                      region_bbox=None) -> dict[str, Any]:
    """Verify every engine cell against the glyph layer inside its own bbox.

    Verdicts per cell: exact / mismatch (both sides have content that differs),
    glyphs_without_engine (ink the engine's cell never captured),
    engine_without_glyphs (text with no ink behind it), empty_agree,
    no_bbox (cell without geometry: unverifiable). Also counts glyphs that sit
    inside no cell at all — dropped rows and columns show up here. The stray
    sweep stays inside `region_bbox` (the table block's own region) when given,
    so captions and neighbouring prose don't read as dropped cells. Mismatches
    keep a bounded sample for review. `per_cell` adds one record per verified
    cell (evaluation harnesses; kept out of production bundles)."""
    counts: Counter[str] = Counter()
    mismatches: list[dict[str, str]] = []
    records: list[dict[str, Any]] = []
    boxes = []
    for c in raw_table.cells:
        if c.bbox is not None:
            boxes.append(_pad_box(c.bbox))
    for cell in raw_table.cells:
        if cell.bbox is None:
            counts["no_bbox"] += 1
            if per_cell:
                records.append({"row": cell.row, "col": cell.col,
                                "status": "no_bbox", "engine": content_norm(cell.text)})
            continue
        chars = pc.region_chars(_read_box(cell.bbox))
        glyphs = content_norm(_reading(chars))
        engine = content_norm(cell.text)
        if not engine and not glyphs:
            counts["empty_agree"] += 1
            status = "empty_agree"
        elif not glyphs:
            counts["engine_without_glyphs"] += 1
            status = "engine_without_glyphs"
        elif not engine:
            counts["glyphs_without_engine"] += 1
            status = "glyphs_without_engine"
        elif engine == glyphs:
            counts["exact"] += 1
            status = "exact"
        elif _squash(engine) == _squash(glyphs):
            # Same content, whitespace placed differently (script-gap drift
            # between the engine's reading and the layer's). Recorded, not
            # flagged: there is nothing here a reviewer would change.
            counts["spacing_only"] += 1
            status = "spacing_only"
        else:
            counts["mismatch"] += 1
            status = "mismatch"
            if len(mismatches) < 6:
                mismatches.append({"engine": engine, "glyphs": glyphs})
        if per_cell:
            records.append({"row": cell.row, "col": cell.col, "status": status,
                            "engine": engine, "glyphs": glyphs})
    uncovered = _uncovered_ink(raw_table, pc, region_bbox)
    result: dict[str, Any] = {
        "cells": dict(counts),
        "uncovered_glyphs": uncovered[0],
        "uncovered_sample": uncovered[1],
        "mismatches": mismatches,
    }
    if per_cell:
        result["records"] = records
    return result


def _uncovered_ink(raw_table: RawTable, pc: PageChars,
                   region_bbox=None) -> tuple[int, list[str]]:
    """Glyphs inside no expanded cell box: the signature of a row, column, or
    cell the engine dropped entirely. When `region_bbox` is given (the table
    block's own region) the sweep is clamped to it, so captions and neighbouring
    prose — outside the region by construction — don't read as dropped cells."""
    boxes = [_pad_box(c.bbox) for c in raw_table.cells if c.bbox is not None]
    if not boxes:
        return 0, []
    raw_boxes = [c.bbox for c in raw_table.cells if c.bbox is not None]
    window = _CELL_PAD_PT + _STRAY_WINDOW_PT
    if region_bbox is not None:
        # Clamp the sweep to the table's own region; captions and body text sit
        # outside it by construction.
        query = BBox(
            x0=max(min(b.x0 for b in raw_boxes) - window,
                   min(region_bbox.x0, region_bbox.x1) - 1.0),
            y0=max(min(b.y0 for b in raw_boxes) - window,
                   min(region_bbox.y0, region_bbox.y1) - 1.0),
            x1=min(max(b.x1 for b in raw_boxes) + window,
                   max(region_bbox.x0, region_bbox.x1) + 1.0),
            y1=min(max(b.y1 for b in raw_boxes) + window,
                   max(region_bbox.y0, region_bbox.y1) + 1.0),
        )
    else:
        query = BBox(
            x0=min(b.x0 for b in raw_boxes) - window,
            y0=min(b.y0 for b in raw_boxes) - window,
            x1=max(b.x1 for b in raw_boxes) + window,
            y1=max(b.y1 for b in raw_boxes) + window,
        )
    strays = [
        c for c in pc.region_chars(query)
        if c[0].strip()
        and not any(b.x0 <= (c[1] + c[3]) / 2 <= b.x1
                    and b.y0 <= (c[2] + c[4]) / 2 <= b.y1 for b in boxes)
    ]
    if not strays:
        return 0, []
    # Group stray chars into snippets by proximity for readable samples.
    strays.sort(key=lambda c: (-(c[2] + c[4]) / 2, c[1]))
    snippets: list[str] = []
    current: list[str] = []
    last = None
    for c in strays:
        if last is not None and ((last[3] + 6 < c[1]) or (last[2] - 8 > c[4])):
            snippets.append("".join(ch[0] for ch in current))
            current = []
        current.append(c)
        last = c
    if current:
        snippets.append("".join(ch[0] for ch in current))
    return len(strays), [" ".join(s.split()) for s in snippets[:8]]
