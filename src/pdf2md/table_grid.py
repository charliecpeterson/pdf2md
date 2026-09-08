"""Structure the emitted cells give away on their own, with no source to consult.

`table_audit.row_accounting` projects the page's own ink and needs the PDF; these
read the grid and nothing else. That is what makes them worth having separately:
the two checks fail for different reasons and corroborate each other when they
agree, so a finding here stands at medium until the accounting confirms it
(`_TEXT_ONLY_KINDS` in `table_audit`). A signature in the text is a suspicion;
ink is evidence.

Every threshold below sits in a band measured over the corpus and the comment
beside it says which. Read those before moving one.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from pdf2md.table_rebuild import compact


@dataclass(frozen=True)
class TableFinding:
    """One thing wrong with a grid: what kind, how sure, and the evidence."""

    kind: str
    severity: str
    detail: str
    rows: tuple[int, ...] = ()

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

# A sign the engine detached from its number. Every character _NUMBER accepts
# as a leading sign, so the two stay in step.
_SIGNS = frozenset("−‑–-")

def _bare_value(cell: str) -> str:
    """A cell's value with the wrapper and unit a publisher put around it removed."""
    text = _VALUE_WRAPPER.sub(r"\1", cell.strip()).strip()
    return _TRAILING_UNIT.sub("", text).strip()

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
        # A published value often carries its unit in the cell: a column of
        # `1.70 A`, `1.55 A` is a column of numbers, and requiring a lone number
        # left it unclassified -- so neither cell check ever looked at it. That is
        # how `l-70 A` (a 1 read as l, a decimal point read as a hyphen) reached a
        # reader unflagged in a 1971 table of van der Waals radii.
        lone = sum(1 for cell in filled if _NUMBER.fullmatch(_bare_value(cell)))
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

def _rejoin_signs(parts: list[str]) -> list[str]:
    """Put a sign back on the number it belongs to, when the cell leads with one.

    The engine renders a page's `\u22123383.702155` as `- 3383.702155`, and a lone
    `-` is not a number, so a cell holding a whole collapsed column of them
    failed the all-numeric test and was skipped entirely. Table 2 of
    s00214-006-0174-5 is the case: ten elements and thirty energies flattened
    into one data row, every column collapsed, and no finding raised.

    Only when the cell begins with a sign, which is what separates a collapsed
    column of negatives (`- v1 - v2 - v3`) from a range (`151 - 153`, an `exp.
    ref` column citing references 151 to 153). Rejoining unconditionally turned
    two such ranges in jp905220k into collapsed rows."""
    if not parts or parts[0] not in _SIGNS:
        return parts
    joined: list[str] = []
    for part in parts:
        if joined and joined[-1] in _SIGNS:
            joined[-1] += part
        else:
            joined.append(part)
    return joined

# A cell in a numeric column that carries digits but is not a number. Pre-1990
# journals set the decimal point as a middle dot, and a scanner that loses it
# turns `4·5` into `45`; the same scan turns `-0·7` into `-@7` and `1·9`
# into `I.9`. Both survive every structural check -- the cell is in the right
# place and its digits are all present -- and both change the value.
_FOOTNOTE_TAIL = re.compile(r"(?<=\d)\s*[a-z]\Z")

_HAS_DIGIT = re.compile(r"\d")

# A corrupted number is short and mostly numeric: `-@7` is one stray in three
# characters. A contents-page cell (`PART 2`, `1.2 The gas laws ... 23`) is neither,
# and a cell of bare digits (`200 202`) is two values merged, which `merged_cells`
# already owns. Without both guards this fired on 18% of corpus tables.
_MAX_CORRUPT_CHARS = 12

_MAX_STRAY_SHARE = 0.5

_NUMERIC_CHARS = frozenset("0123456789.,+-\u2212\u2013\u2011 ")

# A published value often travels with a wrapper or a unit -- `[0.071 V]`, `(3.2)`,
# `1.5 eV`. Neither is a corruption, and both have to come off before the rest of
# the cell is judged, or the check convicts ordinary typesetting.
# The stray has to be a character a reader confuses with a digit, which is the
# failure this detects. Without that, "a cell in a numeric column that is not a
# number" convicts `> 1000`, `Br 2` and `146(i)` -- ordinary typesetting rather
# than damage.
_DIGIT_CONFUSABLE = frozenset("OoDQIlij|ZzSsGbBgq@%xX")

_VALUE_WRAPPER = re.compile(r"\A[\[({]\s*(.*?)\s*[\])}]\Z", re.DOTALL)

_TRAILING_UNIT = re.compile(r"(?<=[\d.])\s+[A-Za-zÅ°%/·\u00b5\u03bc][\w/·^\-]*\Z")

# How much of a numeric column must share one decimal precision before a cell
# lacking it reads as a lost separator rather than a differently-typeset value.
_DECIMAL_MAJORITY = 0.7

_MIN_DECIMAL_CELLS = 6

# A value that lost its separator is bigger than its neighbours by about the power
# of ten it should have carried -- `45` against a column whose median is 2.5 is 18x,
# where 10x is expected. A page number in a column of section numbers (`111` against
# 1.5, 74x) is not, and that distinction is what keeps a textbook contents page out.
_LOST_SEPARATOR_TOLERANCE = 3.0

def _decimal_places(cell: str) -> int | None:
    if not _NUMBER.fullmatch(cell):
        return None
    return len(cell.partition(".")[2])

def _magnitude(cell: str) -> float | None:
    try:
        return abs(float(cell.replace("\u2212", "-").replace("\u2013", "-").replace("\u2011", "-")))
    except ValueError:
        return None

def _percent_columns(rows: list[list[str]], numeric: set[int]) -> set[int]:
    """Columns where a trailing `%` is the unit rather than a stray.

    `0.29%` in a column of percentages is a value; a lone `%` in a column that does
    not otherwise use one is the corruption `-3%` was.
    """
    out = set()
    for col in numeric:
        cells = [row[col].strip() for row in rows if col < len(row) and row[col].strip()]
        if cells and sum(c.endswith("%") for c in cells) >= 0.5 * len(cells):
            out.add(col)
    return out

def _stray_glyph_cells(rows: list[list[str]], numeric: set[int]) -> list[tuple[int, int, str]]:
    """Cells in a numeric column that carry digits and are not numbers.

    A trailing footnote letter is stripped first, because `4.5a` is a value with a
    marker and not a corrupted one -- but only when what remains is a clean number.
    Otherwise the strip manufactures the stray it then reports: `D 4d`, a point
    group, loses its `d` and is convicted for the `D`.
    """
    out = []
    percent = _percent_columns(rows, numeric)
    for index, row in enumerate(rows):
        for col in numeric:
            if col >= len(row):
                continue
            cell = row[col].strip()
            if not cell or not _HAS_DIGIT.search(cell):
                continue
            stripped = _VALUE_WRAPPER.sub(r"\1", cell.strip()).strip()
            stripped = _TRAILING_UNIT.sub("", stripped).strip()
            if col in percent:
                stripped = stripped.rstrip("%").strip()
            marked = _FOOTNOTE_TAIL.sub("", stripped).strip()
            if _NUMBER.fullmatch(marked):
                continue          # a value carrying a footnote marker
            if not stripped or _NUMBER.fullmatch(stripped) or len(stripped) > _MAX_CORRUPT_CHARS:
                continue
            strays = [ch for ch in stripped if ch not in _NUMERIC_CHARS]
            # `2S`, `(5S)`, `4I` are spectroscopic term symbols, and an atomic data
            # compilation is full of them. A label is digits followed by letters with
            # nothing else; a damaged number carries a sign or a separator, which is
            # what `-@7`, `1.oo` and `0.28O` have and a term symbol does not.
            if (strays and all(ch.isalpha() for ch in strays)
                    and not any(ch in ".,+-−–‑" for ch in stripped)
                    and stripped.rstrip("".join(strays)).isdigit()):
                continue
            if (strays and all(ch in _DIGIT_CONFUSABLE for ch in strays)
                    and len(strays) / len(stripped) <= _MAX_STRAY_SHARE):
                out.append((index, col, cell))
    return out

def _lost_separator_columns(rows: list[list[str]], numeric: set[int]) -> list[dict[str, Any]]:
    """Numeric columns where a few cells lack the decimal precision the rest share.

    Two conditions, because either alone is ordinary: most of the column carries
    exactly one precision, and the cells missing it are larger than the rest by
    about the power of ten they should have carried. A column of integers has the
    first and not the second; a column mixing 4.5 and 12 has neither.
    """
    out = []
    for col in sorted(numeric):
        cells = [row[col].strip() for row in rows if col < len(row) and row[col].strip()]
        places = [(cell, _decimal_places(cell)) for cell in cells]
        numbered = [(cell, n) for cell, n in places if n is not None]
        if len(numbered) < _MIN_DECIMAL_CELLS:
            continue
        precise = [(cell, n) for cell, n in numbered if n > 0]
        plain = [cell for cell, n in numbered if n == 0]
        if not precise or not plain or len(plain) >= len(precise):
            continue
        common = max({n for _, n in precise}, key=lambda n: sum(1 for _, m in precise if m == n))
        share = sum(1 for _, n in precise if n == common) / len(numbered)
        if share < _DECIMAL_MAJORITY:
            continue
        typical = sorted(m for m in (_magnitude(c) for c, _ in precise) if m)
        if not typical:
            continue
        middle = typical[len(typical) // 2]
        expected = 10 ** common
        low, high = expected / _LOST_SEPARATOR_TOLERANCE, expected * _LOST_SEPARATOR_TOLERANCE
        suspect = [c for c in plain
                   if low <= (_magnitude(c) or 0) / middle <= high]
        if suspect:
            out.append({"column": col, "places": common, "cells": suspect})
    return out

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
            parts = _rejoin_signs(cell.split())
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
        and any(_NUMBER.search(compact(cell)) for cell in row if cell.strip())
    ]
    if shifted:
        findings.append(TableFinding(
            "shifted_values",
            "high",
            f"{len(shifted)} row(s) carry a single value in a non-leading column with "
            f"every other cell empty, the signature of a value that lost its row",
            tuple(shifted),
        ))

    stray = _stray_glyph_cells(rows, numeric)
    if stray:
        shown = ", ".join(repr(cell) for _, _, cell in stray[:4])
        findings.append(TableFinding(
            "stray_glyphs_in_numeric_column",
            "high",
            f"{len(stray)} cell(s) in numeric column(s) carry digits but are not "
            f"numbers ({shown}) — the value is present and its characters are wrong, "
            f"which no structural check can see",
            tuple(sorted({index for index, _, _ in stray})),
        ))

    lost = _lost_separator_columns(rows, numeric)
    for entry in lost:
        shown = ", ".join(repr(cell) for cell in entry["cells"][:4])
        findings.append(TableFinding(
            "decimal_separator_lost",
            "high",
            f"column {entry['column']} is mostly values with {entry['places']} decimal "
            f"place(s), and {len(entry['cells'])} cell(s) have none while being far "
            f"larger than the rest ({shown}) — the signature of a decimal separator "
            f"the text layer dropped, endemic to middle-dot typesetting",
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
