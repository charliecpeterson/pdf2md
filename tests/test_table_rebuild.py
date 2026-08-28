"""The glyph table rebuild: pure geometry, so synthetic char grids pin the
clustering (lanes from whitespace corridors, rows from visual lines) without
a PDF or an engine. Also the per-engine-cell glyph verification."""

from __future__ import annotations

from pdf2md.schema import BBox, RawCell, RawTable
from pdf2md.scripts import Char
from pdf2md.table_rebuild import check_table_cells, content_norm, locate, rebuild_grid


def ch(text: str, l: float, r: float, b: float, t: float) -> Char:
    return (text, l, b, r, t)


def word(text: str, x: float, b: float, t: float, w: float = 4.0) -> list[Char]:
    """One word as per-char boxes advancing rightward by w."""
    return [ch(c, x + i * w, x + (i + 1) * w, b, t) for i, c in enumerate(text)]


def test_basic_grid_rows_and_lanes():
    # Two rows x three columns; column gaps (10pt) far exceed the min gap,
    # row bands are vertically disjoint.
    bottom, top = 10.0, 20.0
    chars = [
        *word("alpha", 0, bottom, top),
        *word("beta", 30, bottom, top),
        *word("1.5", 60, bottom, top),
        *word("x", 0, 0, 8),
        *word("y2z", 30, 0, 8),
        *word("3", 60, 0, 8),
    ]
    grid, _evidence, refusal = rebuild_grid(chars)
    assert refusal is None and grid is not None
    assert len(grid.lane_bounds) == 3
    assert grid.rows == [
        ["alpha", "beta", "1.5"],
        ["x", "y2z", "3"],
    ]


def test_word_gap_merges_when_another_row_crosses_it():
    # Zero-crossing, not width, decides separators: the header's word space is
    # only a lane boundary if no other row's ink crosses that x-range.
    chars = [
        # Draw order is line by line, as real PDFs emit.
        *word("Training Cost", 0, 20, 30),
        *word("9.6", 70, 20, 30),
        *word("9999", 26, 11, 19),   # a middle row crossing the header's word gap
        *word("value", 0, 0, 8),
        *word("12", 70, 0, 8),
    ]
    grid, _evidence, refusal = rebuild_grid(chars)
    assert refusal is None and grid is not None
    assert len(grid.lane_bounds) == 2
    assert grid.rows[0] == ["Training Cost", "9.6"]


def test_narrow_uncrossed_gap_splits():
    # Conversely a narrow gap nothing crosses IS a boundary (tight numeric
    # columns), even though it is far below any word-space width heuristic.
    chars = [
        *word("ab", 0, 10, 20), *word("cd", 11, 10, 20),
        *word("ef", 0, 0, 8), *word("gh", 11, 0, 8),
    ]
    grid, _evidence, refusal = rebuild_grid(chars)
    assert refusal is None and grid is not None
    assert len(grid.lane_bounds) == 2


def test_refuses_single_column_region():
    chars = [*word("one", 0, 10, 20), *word("two", 0, 0, 8)]
    _grid, _evidence, refusal = rebuild_grid(chars)
    assert refusal == "column_structure_unavailable"


def test_refuses_empty_region():
    _grid, _evidence, refusal = rebuild_grid([])
    assert refusal == "region_has_no_text"


def test_locate_finds_cell_from_a_point():
    chars = [
        *word("ab", 0, 10, 20), *word("cd", 30, 10, 20),
        *word("ef", 0, 0, 8), *word("gh", 30, 0, 8),
    ]
    grid, _evidence, refusal = rebuild_grid(chars)
    assert refusal is None and grid is not None
    # Center of the engine cell for 'cd' on the top row.
    cx, cy = 32.0, 15.0
    pos = locate(grid, cx, cy)
    assert pos is not None
    row, lane = pos
    assert grid.rows[row][lane] == "cd"
    assert locate(grid, 500.0, 500.0) is None


class _FakePC:
    """region_chars() returns the chars whose boxes fall inside the query."""

    def __init__(self, chars: list[Char]) -> None:
        self._chars = chars

    def region_chars(self, bbox) -> list[Char]:
        return [
            c for c in self._chars
            if bbox.x0 <= (c[1] + c[3]) / 2 <= bbox.x1
            and bbox.y0 <= (c[2] + c[4]) / 2 <= bbox.y1
        ]


def _cell(text, x0, x1, y0, y1):
    return RawCell(text=text, bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
                   row=0, col=0, row_span=1, col_span=1, header=False)


def test_content_norm_unifies_typeset_spacing():
    assert content_norm("-2 846.292") == content_norm("-2846.292")
    assert content_norm("2 . 3") == content_norm("2.3")
    assert content_norm("A <sub>2</sub>") == "A 2"
    assert content_norm("ﬁne −1") == "fine -1"


def test_check_table_cells_verdicts():
    # Two cells whose glyphs match (one with typeset spacing), one mismatch,
    # one engine-only invention, and stray ink outside every cell.
    chars = [
        *word("42.05", 0, 10, 20),          # cell 1: exact
        *word("-2", 30, 10, 20), *word("846", 38, 10, 20),   # "-2 846" spaced
        *word("99.9", 60, 10, 20),          # cell 3 glyphs
        ch("?", 98, 100, 12, 18),           # stray ink outside all cells
    ]
    pc = _FakePC(chars)
    raw = RawTable(cells=[
        _cell("42.05", -1, 28, 9, 21),
        _cell("-2 846", 29, 58, 9, 21),     # engine kept the thin space
        _cell("101.5", 59, 88, 9, 21),      # engine disagrees with glyphs
        _cell("", 89, 94, 9, 21),           # empty engine + empty glyphs
    ], num_rows=1, num_cols=4)
    check = check_table_cells(raw, pc)
    assert check["cells"] == {"exact": 2, "mismatch": 1, "empty_agree": 1}
    assert check["uncovered_glyphs"] == 1 and check["uncovered_sample"] == ["?"]
    assert check["mismatches"][0]["engine"] == "101.5"
    assert check["mismatches"][0]["glyphs"] == "99.9"


def test_check_table_cells_flags_one_sided_content():
    chars = [*word("dropped", 0, 10, 20)]
    pc = _FakePC(chars)
    raw = RawTable(cells=[
        _cell("", -1, 28, 9, 21),           # glyphs the engine never captured
    ], num_rows=1, num_cols=1)
    assert check_table_cells(raw, pc)["cells"] == {"glyphs_without_engine": 1}

    raw_invented = RawTable(cells=[
        _cell("ghost", -1, 28, 9, 21),      # text with no ink behind it
    ], num_rows=1, num_cols=1)
    assert check_table_cells(raw_invented, _FakePC([]))["cells"] == {
        "engine_without_glyphs": 1
    }


def test_glyph_unbacked_tables_needs_majority_unbacked():
    from pdf2md.schema import TableData
    from pdf2md.table_rebuild import glyph_unbacked_tables

    def table(block_id, cells):
        return TableData(block_id, 1, None, gfm="| x |", cell_glyph_check={"cells": cells})

    tables = [
        # every text-bearing cell unbacked: a vision-read raster table
        table("#/a", {"engine_without_glyphs": 30}),
        # one stray unbacked cell among exact ones: stays verified
        table("#/b", {"exact": 40, "engine_without_glyphs": 1}),
        # clean
        table("#/c", {"exact": 10, "spacing_only": 2}),
        # borderline majority: unbacked
        table("#/d", {"exact": 3, "engine_without_glyphs": 4}),
    ]
    assert glyph_unbacked_tables(tables) == {"#/a", "#/d"}
