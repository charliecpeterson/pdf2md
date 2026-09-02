"""Audit an extracted table grid for structure the engine got wrong.

Two independent checks that between them cover the row-level failures
`table_rebuild.check_table_cells` is blind to. Per-cell verification compares an
engine cell against the glyphs inside its own bbox, so a row the engine never
created has no cell to check and passes silently.

`row_accounting` measures instead: the source page's own ink projects onto row
bands, and every value a band prints must reach a cell of the engine rows
covering that band. A band whose values reach none is a dropped row; a lane
that loses its value in row after row is a dropped column; a non-header engine
row owning two bands is a merge. `grid_findings` needs no
source at all — a merged pair and a shifted value leave signatures in the
emitted text, which is what makes the two checks worth having separately: they
fail for different reasons and corroborate each other when they agree.

Findings are evidence, like the per-cell verdicts: they flag a table for review
and ride into every artifact derived from it. They never rewrite a cell.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pdf2md.schema import BBox, RawTable
from pdf2md.scripts import Char, PageChars
from pdf2md.table_rebuild import content_norm, engine_lane_bounds, row_bands

# A band has to sit this far inside the engine's cell extent to count as a row
# of the grid; captions and footnotes live in the block's bbox but outside it.
_EXTENT_PAD_PT = 2.0
# How far clear of a column's own edges a value has to sit to be measured.
_LANE_MARGIN_PT = 1.0
# Thousands separators are part of a number; a trailing comma is a list.
_NUMBER = re.compile(r"[−‑–-]?\d(?:[\d,]*\d)?(?:\.\d+)?|[−‑–-]?\.\d+")
# A decimal typeset with its fraction digits in groups: the page prints
# `0.85745` as `0.857 45` and `-14.556089` as `-14.556 089`. That is one value,
# not two collapsed rows, and `table_rebuild.content_norm` already treats such
# in-number spacing as typesetting. Requiring the decimal point is what keeps
# `32 33` and `227 229` -- two integers with no separator convention behind
# them -- reading as the collapse they usually are. Measured over the corpus it
# clears 6 of 102 merged_cells findings and changes none of the rest.
_DECIMAL = re.compile(r"[−‑–+-]?\d+\.\d+")
_DIGIT_GROUP = re.compile(r"\d{2,3}")


def _is_digit_grouped(parts: list[str]) -> bool:
    return (len(parts) > 1 and bool(_DECIMAL.fullmatch(parts[0]))
            and all(_DIGIT_GROUP.fullmatch(part) for part in parts[1:]))
# A column counts as numeric when this share of its filled data cells is a lone
# number. Parameter tables carry the occasional blank or footnote marker.
_NUMERIC_COLUMN_SHARE = 0.6
_MIN_NUMERIC_CELLS = 3
# How many values in one cell make it a collapsed column on its own evidence,
# with no column profile needed.
_MIN_COLLAPSED_VALUES = 4
# A column has to lose its value in at least this many rows (and in half of
# them) before the loss reads as the whole column going missing.
_MIN_DROPPED_COLUMN_ROWS = 3
# How much wider than its box a cell's text may estimate before the cell is
# taken to wrap. Proportional fonts and a ragged right edge make the estimate
# approximate; the cells this needs to catch overrun by multiples, not a few
# per cent.
_WRAP_SLACK = 1.3
# and no cell shorter than this is a wrapped paragraph, whatever its box says.
_MIN_WRAP_CHARS = 40
# How many columns a printed line has to reach before it counts as a row of
# the table rather than one cell's wrapped continuation.
_MIN_ROW_LANES = 2
# How much of its own region a grid has to span before it is read as the whole
# table rather than a fragment of one.
_MIN_REGION_SPAN = 0.5
# Findings that read only the emitted cells; the source can corroborate them.
_TEXT_ONLY_KINDS = frozenset({"merged_cells", "shifted_values", "header_absorbed_data"})


@dataclass(frozen=True)
class TableFinding:
    kind: str
    severity: str
    detail: str
    rows: tuple[int, ...] = ()


@dataclass
class RowAccounting:
    """Source ink rows against engine grid rows: how many of each, which engine
    rows swallowed more than one source row, and what numbers went missing."""

    source_rows: int = 0
    engine_rows: int = 0
    merged: list[dict[str, Any]] = field(default_factory=list)
    lost: list[dict[str, Any]] = field(default_factory=list)
    dropped_columns: list[dict[str, Any]] = field(default_factory=list)
    available: bool = False
    refusal: str | None = None
    lost_refusal: str | None = None


def _covers_little_of(raw: RawTable, region_bbox: BBox | None) -> bool:
    """Whether the engine's cells span materially less than the table's region.

    A healthy grid's cells reach the edges of the block the engine labelled a
    table: across 95 tables from twelve documents the smallest span was 0.79 of
    the region and the median 0.95. The one table below that came in at 0.09 --
    two cells emitted for a table another parser read as ninety-five."""
    boxes = [c.bbox for c in raw.cells if c.bbox is not None]
    if not boxes or region_bbox is None:
        return False
    width = abs(region_bbox.x1 - region_bbox.x0)
    height = abs(region_bbox.y1 - region_bbox.y0)
    if width <= 0 or height <= 0:
        return False
    spanned_x = (max(max(b.x0, b.x1) for b in boxes)
                 - min(min(b.x0, b.x1) for b in boxes))
    spanned_y = (max(max(b.y0, b.y1) for b in boxes)
                 - min(min(b.y0, b.y1) for b in boxes))
    return min(spanned_x / width, spanned_y / height) < _MIN_REGION_SPAN


def _engine_row_bands(raw: RawTable) -> dict[int, tuple[float, float]]:
    bands: dict[int, tuple[float, float]] = {}
    for cell in raw.cells:
        if cell.bbox is None:
            continue
        lo = min(cell.bbox.y0, cell.bbox.y1)
        hi = max(cell.bbox.y0, cell.bbox.y1)
        for row in range(cell.row, cell.row + max(1, cell.row_span)):
            current = bands.get(row)
            bands[row] = (lo, hi) if current is None else (
                min(current[0], lo), max(current[1], hi)
            )
    return bands


def row_accounting(raw: RawTable, pc: PageChars, region_bbox: BBox | None) -> RowAccounting:
    """Project the table region's ink onto rows and account for every value.

    The sweep is clamped to the engine's own cell extent so a caption, footnote,
    or page footer inside the block's bbox is not read as a dropped row."""
    bands = _engine_row_bands(raw)
    if not bands:
        return RowAccounting(refusal="engine_cells_without_geometry")
    if _covers_little_of(raw, region_bbox):
        # The cells span a fraction of the region the engine itself called a
        # table, so the grid is a fragment of it. Everything below clamps its
        # sweep to the cell extent and would compare that fragment against
        # itself, reporting agreement about a table it never saw.
        return RowAccounting(refusal="grid_covers_part_of_region")
    extent_lo = min(lo for lo, _ in bands.values()) - _EXTENT_PAD_PT
    extent_hi = max(hi for _, hi in bands.values()) + _EXTENT_PAD_PT
    query = BBox(
        x0=min(region_bbox.x0, region_bbox.x1) if region_bbox else -1e6,
        y0=extent_lo,
        x1=max(region_bbox.x0, region_bbox.x1) if region_bbox else 1e6,
        y1=extent_hi,
    )
    chars = [
        c for c in pc.region_chars(query)
        if extent_lo <= (c[2] + c[4]) / 2 <= extent_hi
    ]
    # A band with no alphanumeric ink is a rule line or the tail of an underscored
    # atom label that dropped clear of its baseline, not a printed row.
    source_bands = [
        (lo, hi) for lo, hi in row_bands(chars)
        if any(c[0].isalnum() for c in chars if lo <= (c[2] + c[4]) / 2 <= hi)
    ]
    lanes = engine_lane_bounds(raw)
    if not source_bands or not lanes:
        return RowAccounting(refusal="region_has_no_text")
    if len(source_bands) < 2 and len(bands) > 4:
        # A sideways table's glyphs project onto one band; nothing here can be
        # read as rows. Refuse rather than report every value as dropped.
        return RowAccounting(refusal="row_structure_unavailable")

    # A header row legitimately stacks two printed lines ("UFF4MOF" above the
    # column names), so it is excluded from the merge count. So is a row holding
    # a cell too long to print on one line: its wrapped continuations are extra
    # ink bands that belong to the row they are already in. Every other engine
    # row owning more than one printed line collapsed rows that were separate on
    # the page.
    header_rows = _header_rows(raw) | _wrapping_rows(raw, chars)

    engine_lane_text: dict[tuple[int, int], list[str]] = {}
    for cell in raw.cells:
        for row in range(cell.row, cell.row + max(1, cell.row_span)):
            for col in range(cell.col, cell.col + max(1, cell.col_span)):
                engine_lane_text.setdefault((row, col), []).append(cell.text)

    accounting = RowAccounting(
        source_rows=len(source_bands),
        engine_rows=len(bands),
        available=True,
    )
    owned: dict[int, list[str]] = {}
    outside_bands: list[str] = []
    outside_windows: list[tuple[float, float]] = []
    for lo, hi in source_bands:
        centre = (lo + hi) / 2
        covering = [
            row for row, (row_lo, row_hi) in bands.items()
            if row_lo - 1.0 <= centre <= row_hi + 1.0
        ]
        band_chars = [c for c in chars if lo <= (c[2] + c[4]) / 2 <= hi]
        occupied = sum(
            1 for lane_lo, lane_hi in lanes
            if any(lane_lo <= (c[1] + c[3]) / 2 <= lane_hi
                   for c in band_chars if c[0].strip())
        )
        # A printed row spans its table: it puts ink in several columns. A line
        # with ink in one column is the continuation of a cell that wrapped, and
        # counting it as a row of its own turns every paragraph in a table into
        # a pile of merges.
        if (len(covering) == 1 and covering[0] not in header_rows
                and occupied >= _MIN_ROW_LANES):
            owned.setdefault(covering[0], []).append(_band_text(band_chars)[:80])
        # Ink in no column at all. A column the engine dropped outright has no
        # cells, so it has no lane either -- the per-lane comparison below is
        # blind to it by construction, and only its ink gives it away.
        outside = [
            c for c in band_chars
            if c[0].strip()
            and not any(lane_lo <= (c[1] + c[3]) / 2 <= lane_hi
                        for lane_lo, lane_hi in lanes)
        ]
        if outside:
            outside_bands.append(_band_text(outside)[:60])
            outside_windows.append((
                min((c[1] + c[3]) / 2 for c in outside),
                max((c[1] + c[3]) / 2 for c in outside),
            ))
        missing: list[tuple[int, str, bool]] = []
        for col, (lane_lo, lane_hi) in enumerate(lanes):
            lane_chars = [
                c for c in band_chars if lane_lo <= (c[1] + c[3]) / 2 <= lane_hi
            ]
            if not lane_chars:
                continue
            # Ink touching a lane edge may be half of a value the column bounds
            # cut through ("1 . 19" read as a lone "9"). Only a value sitting
            # clear of both edges is measured.
            if (min(c[1] for c in lane_chars) - lane_lo < _LANE_MARGIN_PT
                    or lane_hi - max(c[3] for c in lane_chars) < _LANE_MARGIN_PT):
                continue
            printed = _compact(_band_text(lane_chars))
            # Values only, and only where the lane spells exactly one. A word the
            # engine renders differently from the glyph layer (a dropped accent, a
            # spelled-out ligature) is already a per-cell `mismatch` verdict, and a
            # lane holding several values means the engine's columns don't line up
            # with the page -- neither is a dropped row.
            if not _NUMBER.fullmatch(printed):
                continue
            available = _compact("".join(
                value for row in covering
                for value in engine_lane_text.get((row, col), [])
            ))
            # Whitespace-insensitive containment: the layer splits `1.51` across
            # two runs and the engine joins `2 14` into `214`, and neither is a
            # difference in what the page shows.
            if printed not in available:
                # `available` empty means the engine put nothing in this lane
                # here; non-empty means it put something else. Only the first is
                # a column going missing -- the second is a column that shifted,
                # and calling that "dropped" would name the wrong defect.
                missing.append((col, printed, not available))
        if missing:
            accounting.lost.append({
                "engine_rows": sorted(covering),
                "missing": missing,
                "text": _band_text(band_chars)[:120],
            })
    # A column occupies the same x in every row it prints, and is no wider than
    # the widest column the engine did find. Ink that lands outside the grid at a
    # different x each time, or spread across the width of the table, is the
    # engine's cell boxes falling short of the printed columns -- scattered
    # misalignment, not a column that went missing.
    widest = max(hi - lo for lo, hi in lanes)
    shared = bool(outside_windows) and (
        all(hi - lo <= widest for lo, hi in outside_windows)
        and max(lo for lo, _ in outside_windows) <= min(hi for _, hi in outside_windows)
    )
    if shared and len(outside_bands) >= max(
        _MIN_DROPPED_COLUMN_ROWS, len(source_bands) / 2
    ):
        accounting.dropped_columns.append({
            "column": None,
            "rows": len(outside_bands),
            "values": outside_bands[:6],
        })
    _separate_dropped_columns(accounting, len(source_bands))
    if len(accounting.lost) * 3 > len(source_bands):
        # A third of the rows *still* reading as lost, after whole columns are
        # accounted for, means the engine's columns don't correspond to the
        # page's (side-by-side panels read as one grid, a rotated region, tight
        # leading that merged two printed lines) rather than most of the table
        # going missing. Drop the value-level accounting and keep the row
        # structure, which needs no column alignment.
        accounting.lost = []
        accounting.lost_refusal = "lane_alignment_unavailable"
    accounting.merged = [
        {"engine_row": row, "source_lines": lines}
        for row, lines in sorted(owned.items()) if len(lines) > 1
    ]
    return accounting


def _header_rows(raw: RawTable) -> set[int]:
    """Rows to exclude from the merge count because they are the table's heading.

    Column headers only. `RawCell.header` is also true of a leading label column,
    and a table with one of those marks every row a header -- which was a third
    of the tables measured, every one of them with merge detection silently
    switched off. When the engine names no column header at all, the top row is
    the heading by default: every table has one, and a two-line heading is the
    case this exclusion exists for."""
    columns = {cell.row for cell in raw.cells if cell.column_header}
    return columns or {0}


def _wrapping_rows(raw: RawTable, chars: list[Char]) -> set[int]:
    """Engine rows with a cell whose text cannot fit its own box on one line.

    Row-band counting assumes one printed line per row, which holds for a dense
    parameter table and fails for any table with a paragraph in a cell -- a
    prompt, a model answer, a caption. Counting a wrapped cell's continuation
    lines as collapsed rows reported nine merges for a table that has three
    rows. Measured against the cell's own box and the page's own character
    width, so it needs no threshold beyond the slack for a ragged right edge."""
    ink = [c for c in chars if c[0].strip()]
    if not ink:
        return set()
    width = statistics.median(c[3] - c[1] for c in ink)
    if width <= 0:
        return set()
    wrapping: set[int] = set()
    for cell in raw.cells:
        if cell.bbox is None or not cell.text.strip():
            continue
        box = abs(cell.bbox.x1 - cell.bbox.x0)
        if box <= 0:
            continue
        text = content_norm(cell.text)
        # Both conditions. A cell can overrun a narrow numeric column at eleven
        # characters -- `0.965 0.969` does -- and no eleven-character cell is a
        # wrapped paragraph. Excluding those rows threw out every row of a table
        # whose columns were merely narrow, which is the opposite of the point.
        if len(text) >= _MIN_WRAP_CHARS and len(text) * width > box * _WRAP_SLACK:
            wrapping.update(
                range(cell.row, cell.row + max(1, cell.row_span))
            )
    return wrapping


def _dropped_column_detail(entry: dict[str, Any]) -> str:
    sample = ", ".join(repr(value) for value in entry["values"][:4])
    if entry["column"] is None:
        return (
            f"{entry['rows']} row(s) of this region print text that lies outside "
            f"every column of the grid, the signature of a column the engine never "
            f"created: {sample}"
        )
    return (
        f"column {entry['column']} prints a value in {entry['rows']} row(s) that "
        f"reaches no cell of the grid: {sample}"
    )


def _separate_dropped_columns(accounting: RowAccounting, source_rows: int) -> None:
    """Split whole missing columns out of the per-row losses.

    A column the engine never created loses a value in every row at once. Left
    in the per-row tally that looks like most of the table going missing, which
    is exactly the shape the misalignment refusal suppresses -- so a dropped
    column, the clearest defect of the lot, would be the one finding never
    reported. Pulled out here it is named once, and what remains is the scatter
    the refusal is actually about."""
    per_column: Counter[int] = Counter()
    occupied: set[int] = set()
    for entry in accounting.lost:
        per_column.update({col for col, _, _ in entry["missing"]})
        occupied.update(col for col, _, empty in entry["missing"] if not empty)
    floor = max(_MIN_DROPPED_COLUMN_ROWS, source_rows / 2)
    # A column whose cells hold *something* in any of the rows that lost a value
    # is misaligned, not missing.
    dropped = {
        col for col, rows in per_column.items()
        if rows >= floor and col not in occupied
    }
    if not dropped:
        for entry in accounting.lost:
            entry["missing"] = [value for _, value, _ in entry["missing"]][:12]
        return

    samples: dict[int, list[str]] = {col: [] for col in dropped}
    remaining: list[dict[str, Any]] = []
    for entry in accounting.lost:
        kept: list[str] = []
        for col, value, _empty in entry["missing"]:
            if col in dropped:
                if len(samples[col]) < 6:
                    samples[col].append(value)
            else:
                kept.append(value)
        if kept:
            entry["missing"] = kept[:12]
            remaining.append(entry)
    accounting.lost = remaining
    accounting.dropped_columns = [
        {"column": col, "rows": per_column[col], "values": samples[col]}
        for col in sorted(dropped)
    ]


def _compact(text: str) -> str:
    """Comparable content with every space gone. Column-aligned typesetting puts
    spaces inside numbers and the engine puts them elsewhere; nothing about
    either placement is content."""
    return re.sub(r"\s+", "", content_norm(text))


def _band_text(chars: list[Char]) -> str:
    ink = sorted((c for c in chars if c[0].strip()), key=lambda c: c[1])
    if not ink:
        return ""
    # Word gaps become spaces the same way table_rebuild._reading does it:
    # PDFs draw no space glyphs but engine cell text has them.
    height = max(c[4] - c[2] for c in ink)
    out: list[str] = []
    prev = None
    for c in ink:
        if prev is not None and c[1] - prev[3] > 0.22 * height:
            out.append(" ")
        out.append(c[0])
        prev = c
    return "".join(out)


def _numeric_columns(rows: list[list[str]]) -> set[int]:
    width = max((len(row) for row in rows), default=0)
    numeric: set[int] = set()
    for col in range(width):
        filled = [
            row[col].strip() for row in rows
            if col < len(row) and row[col].strip()
        ]
        if len(filled) < _MIN_NUMERIC_CELLS:
            continue
        lone = sum(1 for cell in filled if _NUMBER.fullmatch(cell))
        if lone >= _NUMERIC_COLUMN_SHARE * len(filled):
            numeric.add(col)
    return numeric


def _uniformly_collapsed_columns(rows: list[list[str]]) -> set[int]:
    """Columns whose cells all hold the same count of values, more than one.

    `_numeric_columns` needs most of a column's cells to be a lone number, so it
    cannot see a column where *every* cell was collapsed -- none is ever lone,
    and the column never qualifies as numeric at all. Consistency is the signal
    instead: a whole column of cells each holding exactly two numbers is two
    columns, or two rows, that were merged into one."""
    width = max((len(row) for row in rows), default=0)
    collapsed: set[int] = set()
    for col in range(width):
        counts = [
            len(parts)
            for row in rows
            if col < len(row) and (parts := row[col].split())
            and all(_NUMBER.fullmatch(part) for part in parts)
        ]
        if len(counts) < _MIN_NUMERIC_CELLS:
            continue
        common = Counter(counts).most_common(1)[0]
        if common[0] > 1 and common[1] >= _NUMERIC_COLUMN_SHARE * len(counts):
            collapsed.add(col)
    return collapsed


def grid_findings(header: list[str], rows: list[list[str]]) -> list[TableFinding]:
    """Structure the emitted cells give away on their own, no source needed."""
    findings: list[TableFinding] = []
    numeric = _numeric_columns(rows)
    collapsed_columns = _uniformly_collapsed_columns(rows)
    if not rows:
        return findings

    merged: list[str] = []
    merged_rows: list[int] = []
    for index, row in enumerate(rows):
        for col, raw_cell in enumerate(row):
            cell = raw_cell.strip()
            # Whitespace-separated only: "1.380, 1.526" is one cell listing two
            # published values, "1.478 1.338" is two rows collapsed into one.
            parts = cell.split()
            if len(parts) < 2 or not all(_NUMBER.fullmatch(part) for part in parts):
                continue
            if _is_digit_grouped(parts):  # one value, typeset in digit groups
                continue
            # Two values are only suspicious where the column holds one apiece.
            # Many values in a single cell need no such context: a cell holding
            # a whole column of numbers is that column collapsed, and a grid
            # collapsed to one data row has no column profile left to compare
            # against -- which is exactly when this is the only signal there is.
            if (col in numeric or col in collapsed_columns
                    or len(parts) >= _MIN_COLLAPSED_VALUES):
                merged.append(cell)
                merged_rows.append(index)
    if merged:
        collapsed = max(len(cell.split()) for cell in merged)
        detail = (
            f"{len(merged)} cell(s) hold up to {collapsed} whitespace-separated "
            f"values each, the signature of a whole column collapsed into one cell"
            if collapsed >= _MIN_COLLAPSED_VALUES else
            f"{len(merged)} cell(s) in otherwise single-value numeric columns hold "
            f"several whitespace-separated numbers, the signature of rows the engine "
            f"collapsed into one"
        )
        findings.append(TableFinding(
            "merged_cells",
            "high",
            f"{detail}: {', '.join(repr(cell[:60]) for cell in merged[:4])}",
            tuple(sorted(set(merged_rows))),
        ))

    # The lone cell has to carry a value. A header that wraps across several grid
    # rows leaves rows holding one label fragment -- `(%)*`, `No. of`, `Embryo
    # develop` -- and those are the header being itself, not a value that lost
    # its row. Carrying a number, not being one: `c = 8.95` is a lattice
    # parameter that lost its row and reads nothing like a header fragment.
    shifted = [
        index for index, row in enumerate(rows)
        if sum(1 for cell in row if cell.strip()) == 1
        and not (row and row[0].strip())
        and any(_NUMBER.search(_compact(cell)) for cell in row if cell.strip())
    ]
    if shifted:
        findings.append(TableFinding(
            "shifted_values",
            "high",
            f"{len(shifted)} row(s) carry a single value in a non-leading column with "
            f"every other cell empty, the signature of a value that lost its row",
            tuple(shifted),
        ))

    absorbed = _absorbed_header_columns(header, rows, numeric, _column_values(rows))
    if absorbed:
        findings.append(TableFinding(
            "header_absorbed_data",
            "high",
            f"the header region mixes labels and data values in numeric column(s) "
            f"{', '.join(str(col) for col in absorbed)}, the signature of a first data "
            f"row the engine folded into the header",
        ))
    return findings


def _column_values(rows: list[list[str]]) -> dict[int, list[str]]:
    values: dict[int, list[str]] = {}
    for row in rows:
        for col, cell in enumerate(row):
            cell = cell.strip()
            if _NUMBER.fullmatch(cell):
                values.setdefault(col, []).append(cell)
    return values


def _absorbed_header_columns(
    header: list[str],
    rows: list[list[str]],
    numeric: set[int],
    values: dict[int, list[str]],
) -> list[int]:
    """Numeric columns whose header region carries both a label and a value.

    Checks the header row and the first body row: an engine that folds the
    opening data row into its header leaves the values in whichever of the two
    the serializer called a header. Two guards keep a label's own digits out: the
    number has to be *shaped* like the column's values, and it has to be the last
    thing in the cell, which is where an absorbed row's value lands. Between them
    they exclude the subscript zero in `angle θ 0 (deg)`, the 4 inside `UFF4MOF`,
    the exponent in `cell vol (Å 3 )`, and the temperature opening `393 K F MD`."""
    absorbed: list[int] = []
    for cells in (header, rows[0] if rows else []):
        for col in sorted(numeric):
            if col >= len(cells) or col in absorbed:
                continue
            tokens = cells[col].split()
            if len(tokens) < 2 or not any(ch.isalpha() for ch in cells[col]):
                continue
            column = values.get(col, [])
            decimal_column = sum("." in value for value in column) * 2 >= len(column)
            if (_NUMBER.fullmatch(tokens[-1])
                    and ("." in tokens[-1]) == decimal_column):
                absorbed.append(col)
    return sorted(absorbed)


def raster_row_findings(crop_path: Path, engine_rows: int) -> dict[str, Any]:
    """Row accounting for a table with no text layer, read off its own crop.

    Everything else here needs glyph geometry, so on a scanned page the whole
    row-level apparatus refuses and a dropped row goes unreported -- on exactly
    the documents where extraction is worst. The rendered crop carries the same
    evidence in pixels, and `row_locator.projection_row_bands` already knows how
    to read it: it projects ink in the panel's *leading stripe*, which is where
    row labels live. That detail is what makes it usable here for free -- a cell
    that wrapped in a right-hand column puts no ink in the label stripe, so its
    continuation lines cannot be mistaken for rows the way they were in the
    glyph path."""
    if engine_rows < 1 or not crop_path.is_file():
        return {}
    from PIL import Image

    from pdf2md.row_locator import projection_row_bands

    with Image.open(crop_path) as image:
        _bands, evidence, refusal = projection_row_bands(
            image.convert("L"), engine_rows
        )
    printed = int(evidence.get("text_bands", 0))
    payload: dict[str, Any] = {
        "rows": {"source": printed, "engine": engine_rows, "read": "raster"},
    }
    if refusal and refusal != "projection_row_count_mismatch":
        payload["rows_refusal"] = refusal
        return payload
    if printed > engine_rows:
        payload["findings"] = [{
            "kind": "row_count",
            "severity": "medium",
            "detail": (
                f"the scanned table's label column prints {printed} rows and the "
                f"grid has {engine_rows}; the cells cannot be checked against a "
                f"text layer, so the crop is the record"
            ),
        }]
    return payload


def audit_table(
    header: list[str],
    rows: list[list[str]],
    raw: RawTable | None,
    pc: PageChars | None,
    region_bbox: BBox | None,
) -> dict[str, Any]:
    """The grid's findings plus, when the page has a text layer, its row
    accounting. Returns the payload stored on `TableData.grid_audit`; an empty
    dict means nothing to report.

    A text-only signature stands at medium on its own and rises to high when the
    row accounting independently measures a merge or a loss in the same table.
    An index legitimately lists two page numbers in one cell; a parameter table
    whose glyph rows also fail to line up does not."""
    findings = grid_findings(header, rows)
    payload: dict[str, Any] = {}
    corroborated = False
    if raw is not None and pc is not None:
        accounting = row_accounting(raw, pc, region_bbox)
        if accounting.available:
            payload["rows"] = {
                "source": accounting.source_rows,
                "engine": accounting.engine_rows,
            }
            if accounting.lost:
                lost = sum(len(entry["missing"]) for entry in accounting.lost)
                findings.append(TableFinding(
                    "dropped_row_content",
                    "high",
                    f"{len(accounting.lost)} printed row(s) carry {lost} number(s) that "
                    f"reach no cell of the grid: "
                    + "; ".join(
                        f"{entry['text']!r} is missing {', '.join(entry['missing'])}"
                        for entry in accounting.lost[:3]
                    ),
                ))
                payload["lost_rows"] = accounting.lost[:12]
            if accounting.dropped_columns:
                findings.append(TableFinding(
                    "dropped_column",
                    "high",
                    "; ".join(_dropped_column_detail(entry)
                              for entry in accounting.dropped_columns),
                ))
                payload["dropped_columns"] = accounting.dropped_columns
            if accounting.lost_refusal:
                payload["lost_rows_refusal"] = accounting.lost_refusal
            if accounting.merged:
                findings.append(TableFinding(
                    "merged_rows",
                    "high",
                    f"{len(accounting.merged)} grid row(s) cover more than one printed "
                    f"row of the source; the page shows {accounting.source_rows} rows in "
                    f"this region and the grid has {accounting.engine_rows}: "
                    + "; ".join(
                        " + ".join(repr(line) for line in entry["source_lines"])
                        for entry in accounting.merged[:3]
                    ),
                    tuple(entry["engine_row"] for entry in accounting.merged),
                ))
                payload["merged_rows"] = accounting.merged[:12]
            corroborated = bool(
                accounting.lost or accounting.merged or accounting.dropped_columns
            )
        elif accounting.refusal == "grid_covers_part_of_region":
            payload["rows_refusal"] = accounting.refusal
            findings.append(TableFinding(
                "partial_grid",
                "high",
                "the engine's cells span less than half the region it labelled a "
                "table, so the grid is a fragment of it and the rest of the table "
                "reached no cell",
            ))
        elif accounting.refusal:
            payload["rows_refusal"] = accounting.refusal

    if not corroborated:
        findings = [
            TableFinding(f.kind, "medium", f.detail, f.rows)
            if f.severity == "high" and f.kind in _TEXT_ONLY_KINDS else f
            for f in findings
        ]
    payload["corroborated"] = corroborated
    if findings:
        payload["findings"] = [
            {
                "kind": finding.kind,
                "severity": finding.severity,
                "detail": finding.detail,
                **({"rows": list(finding.rows)} if finding.rows else {}),
            }
            for finding in findings
        ]
    return payload
