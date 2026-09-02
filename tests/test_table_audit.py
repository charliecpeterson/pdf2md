"""Row- and grid-level table findings. The grid checks read only the emitted
cells, so they take plain row lists; the row accounting is pure geometry, so a
synthetic char layer and cell boxes pin it without a PDF or an engine."""

from __future__ import annotations

from pdf2md.schema import BBox, RawCell, RawTable
from pdf2md.table_audit import audit_table, grid_findings, row_accounting


def word(text: str, x: float, baseline: float, w: float = 2.0, h: float = 7.0):
    """One word as per-char boxes advancing rightward from x on a baseline.

    Narrow enough that a cell's text fits its box: a real table's cells do, and
    the audit refuses to count merges in a row whose cell has to wrap."""
    return [
        (c, x + i * w, baseline, x + (i + 1) * w, baseline + h)
        for i, c in enumerate(text)
    ]


class Chars:
    def __init__(self, chars):
        self._chars = chars

    def region_chars(self, bbox):
        left, right = min(bbox.x0, bbox.x1), max(bbox.x0, bbox.x1)
        bottom, top = min(bbox.y0, bbox.y1), max(bbox.y0, bbox.y1)
        return [
            c for c in self._chars
            if left - 1 <= (c[1] + c[3]) / 2 <= right + 1
            and bottom - 1 <= (c[2] + c[4]) / 2 <= top + 1
        ]


def cell(text: str, row: int, col: int, baseline: float, x: float, w: float = 30.0):
    return RawCell(
        text=text,
        bbox=BBox(x0=x, y0=baseline - 1, x1=x + w, y1=baseline + 8),
        row=row, col=col, row_span=1, col_span=1, header=row == 0,
    )


def kinds(findings):
    return {f["kind"]: f for f in findings}


def test_merged_cell_signature_needs_whitespace_not_a_comma_list():
    rows = [
        ["Na4f2", "1.79 1.525"],
        ["Al3f2", "1.28"],
        ["K_3f2", "2.38"],
        ["Ca3f2", "1.705"],
    ]
    found = kinds(f.__dict__ for f in grid_findings(["type", "r"], rows))
    assert "merged_cells" in found
    assert "1.79 1.525" in found["merged_cells"]["detail"]

    # A published cell listing two values separated by a comma is content.
    listed = [["W_3f2", "1.380, 1.526"], *rows[1:]]
    assert "merged_cells" not in kinds(f.__dict__ for f in grid_findings(["type", "r"], listed))


def test_shifted_row_is_a_lone_value_outside_the_label_column():
    rows = [
        ["Ag3f2", "109.47", "1.48"],
        ["", "", "1.51"],
        ["Cd1f1", "180.0", "1.40"],
        ["Cd3f2", "109.47", "1.29"],
    ]
    found = kinds(f.__dict__ for f in grid_findings(["type", "angle", "r"], rows))
    assert found["shifted_values"]["rows"] == (1,)

    # A lone label in the leading column is a section heading inside the table.
    heading = [["group I", "", ""], *rows[:1], *rows[2:]]
    assert "shifted_values" not in kinds(
        f.__dict__ for f in grid_findings(["type", "angle", "r"], heading)
    )


def test_header_absorbing_a_data_row_is_a_trailing_value_shaped_like_the_column():
    rows = [
        ["new atom types", "bond r I (Å) 1.28"],
        ["Na3f2", "1.623"],
        ["Al3f2", "1.28"],
        ["K_3f2", "2.38"],
    ]
    found = kinds(f.__dict__ for f in grid_findings(["", "bond r (Å)"], rows))
    assert found["header_absorbed_data"]["kind"] == "header_absorbed_data"


def test_digits_inside_a_label_are_not_absorbed_data():
    # A subscript zero, an exponent, and a leading temperature all sit in header
    # labels of numeric columns and none of them is a stray value.
    for label in ("angle θ 0 (deg)", "cell vol (Å 3 )", "393 K F MD"):
        rows = [[label, "109.47"], ["Na3f2", "109.47"], ["Al3f2", "90.0"],
                ["K_3f2", "120.0"]]
        found = kinds(f.__dict__ for f in grid_findings(["type", "angle"], rows))
        assert "header_absorbed_data" not in found, label


def _two_column_layer(lines):
    """Lines of (label, value) drawn top-down at 11pt leading from y=100. Text
    sits inset from its column bounds, as typeset cells do."""
    chars = []
    for i, (label, value) in enumerate(lines):
        baseline = 100.0 - 11.0 * i
        chars.extend(word(label, 2.0, baseline))
        chars.extend(word(value, 42.0, baseline))
    return Chars(chars)


def test_row_accounting_reports_a_row_the_grid_never_created():
    layer = _two_column_layer([
        ("Ag1f1", "1.22"),
        ("Ag2f2", "1.34"),
        ("Ag3f2", "1.48"),
    ])
    # The engine kept Ag1f1 and Ag3f2 and never made a row for Ag2f2.
    raw = RawTable(
        cells=[
            cell("Ag1f1", 0, 0, 100.0, 0.0), cell("1.22", 0, 1, 100.0, 40.0),
            cell("Ag3f2", 1, 0, 78.0, 0.0), cell("1.48", 1, 1, 78.0, 40.0),
        ],
        num_rows=2, num_cols=2,
    )
    accounting = row_accounting(raw, layer, BBox(x0=-5, y0=70, x1=80, y1=115))
    assert accounting.available
    assert accounting.source_rows == 3 and accounting.engine_rows == 2
    assert [entry["missing"] for entry in accounting.lost] == [["1.34"]]


def test_row_accounting_is_silent_on_a_grid_that_matches_the_page():
    layer = _two_column_layer([("Ag1f1", "1.22"), ("Ag3f2", "1.48")])
    raw = RawTable(
        cells=[
            cell("Ag1f1", 0, 0, 100.0, 0.0), cell("1.22", 0, 1, 100.0, 40.0),
            cell("Ag3f2", 1, 0, 89.0, 0.0), cell("1.48", 1, 1, 89.0, 40.0),
        ],
        num_rows=2, num_cols=2,
    )
    accounting = row_accounting(raw, layer, BBox(x0=-5, y0=85, x1=80, y1=115))
    assert accounting.available
    assert accounting.lost == [] and accounting.merged == []


def test_row_accounting_names_the_grid_row_that_swallowed_two_printed_rows():
    layer = _two_column_layer([
        ("header", "value"),
        ("Na4f2", "1.79"),
        ("Mg6f3", "1.525"),
        ("Al3f2", "1.28"),
    ])
    # One data row's cells span both printed rows; both values survive, so this
    # is a merge and not a loss.
    merged = RawCell("Na4f2 Mg6f3", BBox(x0=0.0, y0=77.0, x1=30.0, y1=97.0),
                     1, 0, 1, 1, False)
    merged_value = RawCell("1.79 1.525", BBox(x0=40.0, y0=77.0, x1=70.0, y1=97.0),
                           1, 1, 1, 1, False)
    raw = RawTable(
        cells=[
            cell("header", 0, 0, 100.0, 0.0), cell("value", 0, 1, 100.0, 40.0),
            merged, merged_value,
            cell("Al3f2", 2, 0, 67.0, 0.0), cell("1.28", 2, 1, 67.0, 40.0),
        ],
        num_rows=3, num_cols=2,
    )
    accounting = row_accounting(raw, layer, BBox(x0=-5, y0=60, x1=80, y1=115))
    assert accounting.lost == []
    assert [entry["engine_row"] for entry in accounting.merged] == [1]
    assert len(accounting.merged[0]["source_lines"]) == 2


def test_a_text_signature_alone_is_medium_and_rises_when_the_page_agrees():
    rows = [["Na4f2", "1.79 1.525"], ["Al3f2", "1.28"], ["K_3f2", "2.38"],
            ["Ca3f2", "1.705"]]
    alone = audit_table(["type", "r"], rows, None, None, None)
    assert kinds(alone["findings"])["merged_cells"]["severity"] == "medium"

    layer = _two_column_layer([
        ("header", "value"),
        ("Na4f2", "1.79"),
        ("Mg6f3", "1.525"),
    ])
    raw = RawTable(
        cells=[
            cell("header", 0, 0, 100.0, 0.0), cell("value", 0, 1, 100.0, 40.0),
            RawCell("Na4f2 Mg6f3", BBox(x0=0.0, y0=77.0, x1=30.0, y1=97.0),
                    1, 0, 1, 1, False),
            RawCell("1.79 1.525", BBox(x0=40.0, y0=77.0, x1=70.0, y1=97.0),
                    1, 1, 1, 1, False),
        ],
        num_rows=2, num_cols=2,
    )
    corroborated = audit_table(
        ["type", "r"], rows, raw, layer, BBox(x0=-5, y0=70, x1=80, y1=115)
    )
    assert kinds(corroborated["findings"])["merged_cells"]["severity"] == "high"
    assert corroborated["rows"] == {"source": 3, "engine": 2}


def test_a_region_read_sideways_refuses_rather_than_reporting_every_row_lost():
    # One band of ink against a grid of many rows: the projection found no row
    # structure, so there is nothing trustworthy to say about it.
    layer = Chars(word("sideways", 2.0, 100.0))
    raw = RawTable(
        cells=[cell(f"r{i}", i, 0, 100.0 - 11.0 * i, 0.0) for i in range(6)],
        num_rows=6, num_cols=1,
    )
    # The region matches the cells: this is about the ink, not the grid's reach.
    accounting = row_accounting(raw, layer, BBox(x0=-1, y0=30, x1=31, y1=115))
    assert not accounting.available
    assert accounting.refusal == "row_structure_unavailable"


def _three_column_layer(lines):
    """Lines of (label, left, right) at 11pt leading from y=100, text inset from
    its column bounds the way typeset cells are."""
    chars = []
    for i, (label, left, right) in enumerate(lines):
        baseline = 100.0 - 11.0 * i
        chars.extend(word(label, 2.0, baseline))
        chars.extend(word(left, 42.0, baseline))
        chars.extend(word(right, 82.0, baseline))
    return Chars(chars)


def _two_of_three_grid(rows):
    """An engine grid that kept the label and left columns and dropped the right."""
    cells = []
    for index, (label, left) in enumerate(rows):
        baseline = 100.0 - 11.0 * index
        cells.append(cell(label, index, 0, baseline, 0.0))
        cells.append(cell(left, index, 1, baseline, 40.0))
    return RawTable(cells=cells, num_rows=len(rows), num_cols=2)


def test_a_column_the_grid_never_created_is_named_once_not_row_by_row():
    printed = [("header", "left", "right"),
               ("Ag1f1", "1.22", "1.38"),
               ("Ag3f2", "1.48", "1.44"),
               ("Cd1f1", "1.40", "1.29"),
               ("Cd4f2", "1.46", "1.64")]
    accounting = row_accounting(
        _two_of_three_grid([(a, b) for a, b, _ in printed]),
        _three_column_layer(printed),
        BBox(x0=-5, y0=50, x1=120, y1=115),
    )
    assert accounting.available
    # The dropped column has no cells, so it has no lane: its ink is what gives
    # it away, and `column` is None because the grid has no index for it.
    assert [entry["column"] for entry in accounting.dropped_columns] == [None]
    assert accounting.dropped_columns[0]["rows"] == 5
    # Pulled out of the per-row tally, so what's left is not the misalignment
    # scatter the refusal exists for.
    assert accounting.lost == []
    assert accounting.lost_refusal is None


def test_a_dropped_column_survives_the_misalignment_refusal():
    # Four of five rows losing a value would trip the one-third refusal if the
    # losses were not first recognised as one column.
    printed = [("header", "left", "right"),
               ("Ag1f1", "1.22", "1.38"),
               ("Ag3f2", "1.48", "1.44"),
               ("Cd1f1", "1.40", "1.29"),
               ("Cd4f2", "1.46", "1.64")]
    out = audit_table(
        ["type", "left"],
        [[a, b] for a, b, _ in printed[1:]],
        _two_of_three_grid([(a, b) for a, b, _ in printed]),
        _three_column_layer(printed),
        BBox(x0=-5, y0=50, x1=120, y1=115),
    )
    found = kinds(out["findings"])
    assert found["dropped_column"]["severity"] == "high"
    assert "outside every column of the grid" in found["dropped_column"]["detail"]


def test_a_wrapped_cell_is_not_a_pile_of_merged_rows():
    # A table of prompts or model answers has a paragraph in a cell. Its wrapped
    # continuation lines are ink bands belonging to the row they are already in;
    # counting them as collapsed rows reported nine merges for a three-row table,
    # and was the most common finding on a corpus of unseen papers.
    answer = "the quick brown fox jumps over the lazy dog and keeps running on"
    chars = [
        *word("Model", 2.0, 100.0), *word("Answer", 42.0, 100.0),
        *word("ModelA", 2.0, 89.0), *word(answer[:30], 42.0, 89.0),
        *word(answer[30:], 42.0, 78.0),          # wrapped continuation
        *word("ModelB", 2.0, 67.0), *word(answer[:30], 42.0, 67.0),
        *word(answer[30:], 42.0, 56.0),          # wrapped continuation
    ]
    raw = RawTable(
        cells=[
            cell("Model", 0, 0, 100.0, 0.0), cell("Answer", 0, 1, 100.0, 40.0),
            cell("ModelA", 1, 0, 89.0, 0.0),
            RawCell(answer, BBox(x0=40.0, y0=77.0, x1=70.0, y1=97.0), 1, 1, 1, 1, False),
            cell("ModelB", 2, 0, 67.0, 0.0),
            RawCell(answer, BBox(x0=40.0, y0=55.0, x1=70.0, y1=75.0), 2, 1, 1, 1, False),
        ],
        num_rows=3, num_cols=2,
    )
    accounting = row_accounting(raw, Chars(chars), BBox(x0=-5, y0=50, x1=80, y1=115))
    assert accounting.available
    # The page really does print more lines than the grid has rows, and that is
    # recorded — it just isn't a defect.
    assert accounting.source_rows == 5 and accounting.engine_rows == 3
    assert accounting.merged == []


def test_a_printed_row_has_to_reach_more_than_one_column():
    # Two columns of prose wrapping together defeat a width estimate: each cell
    # looks like it fits. What gives the continuation away is that it reaches
    # only the column it belongs to.
    chars = [
        *word("Step", 2.0, 100.0), *word("Detail", 42.0, 100.0),
        *word("Step1", 2.0, 89.0), *word("search for the song", 42.0, 89.0),
        *word("and then find it", 42.0, 78.0),   # continuation: one column only
        *word("Step2", 2.0, 67.0), *word("answer the question", 42.0, 67.0),
    ]
    raw = RawTable(
        cells=[
            cell("Step", 0, 0, 100.0, 0.0), cell("Detail", 0, 1, 100.0, 40.0),
            cell("Step1", 1, 0, 89.0, 0.0),
            RawCell("search for the song and then find it",
                    BBox(x0=40.0, y0=77.0, x1=70.0, y1=97.0), 1, 1, 1, 1, False),
            cell("Step2", 2, 0, 67.0, 0.0), cell("answer the question", 2, 1, 67.0, 40.0),
        ],
        num_rows=3, num_cols=2,
    )
    accounting = row_accounting(raw, Chars(chars), BBox(x0=-5, y0=50, x1=80, y1=115))
    assert accounting.merged == []


def test_a_scanned_table_is_measured_from_its_crop(tmp_path):
    # No text layer, so every glyph-based check refuses. The crop still shows how
    # many rows the label column prints.
    from PIL import Image, ImageDraw

    from pdf2md.table_audit import raster_row_findings

    image = Image.new("L", (200, 130), color=255)
    draw = ImageDraw.Draw(image)
    for i in range(6):  # six printed label rows down the leading stripe
        draw.rectangle([6, 10 + i * 20, 40, 10 + i * 20 + 8], fill=0)
        draw.rectangle([90, 10 + i * 20, 180, 10 + i * 20 + 8], fill=0)
    path = tmp_path / "table.png"
    image.save(path)

    # A grid that kept four of the six.
    found = raster_row_findings(path, 4)
    assert found["rows"] == {"source": 6, "engine": 4, "read": "raster"}
    assert [f["kind"] for f in found["findings"]] == ["row_count"]
    assert "prints 6 rows" in found["findings"][0]["detail"]

    # A grid that kept all six says nothing.
    assert "findings" not in raster_row_findings(path, 6)


def test_a_grid_that_is_a_fragment_of_its_table_is_refused_not_confirmed():
    # Two cells emitted for a table that fills the block. Clamping the sweep to
    # the cell extent would compare that fragment against itself and report
    # agreement about a table the audit never saw.
    layer = _two_column_layer([(f"row{n}", f"1.{n}") for n in range(6)])
    raw = RawTable(
        cells=[cell("row0", 0, 0, 100.0, 0.0), cell("1.0", 0, 1, 100.0, 40.0)],
        num_rows=1, num_cols=2,
    )
    region = BBox(x0=-5, y0=30, x1=80, y1=115)
    accounting = row_accounting(raw, layer, region)
    assert not accounting.available
    assert accounting.refusal == "grid_covers_part_of_region"

    out = audit_table(["type", "r"], [["row0", "1.0"]], raw, layer, region)
    assert kinds(out["findings"])["partial_grid"]["severity"] == "high"


def test_a_wrapped_header_is_not_a_shifted_value():
    # A header spanning several grid rows leaves rows holding one label fragment.
    # Two engines agreed cell for cell on such a table and the audit flagged it.
    header_fragments = [
        ["", "", "", "No. of", "", "", ""],
        ["Time after IVF", "No. of oocytes", "No. of MII", "", "", "", ""],
        ["", "", "", "(%)*", "", "", ""],
        ["12", "103 (9)", "63 (61.2)", "28.6", "5", "13", "0"],
        ["18", "97 (7)", "65 (67.0)", "50.8", "3", "30", "0"],
        ["24", "91 (7)", "59 (64.9)", "49.2", "4", "25", "0"],
    ]
    found = kinds(f.__dict__ for f in grid_findings([""] * 7, header_fragments))
    assert "shifted_values" not in found

    # A value alone in a non-leading column still is one.
    with_value = [*header_fragments, ["", "", "", "1.51", "", "", ""]]
    found = kinds(f.__dict__ for f in grid_findings([""] * 7, with_value))
    assert found["shifted_values"]["rows"] == (6,)


def test_a_column_collapsed_into_one_cell_needs_no_column_profile():
    # A table flattened to one data row: each cell holds its whole column. The
    # numeric-column test cannot see this — with one data row there is no column
    # profile to compare against — and the row-band check is suppressed by the
    # wrapped-cell guard, because a cell holding eleven rows certainly overruns
    # its box. The cell's own contents are the only evidence left.
    rows = [[
        "Caption Footnote Formula List-item",
        "84.1 83.9 83.5 87.2 93.4 85.9 69.1",
        "68.4 71.5 70.9 71.8 60.1 63.4 81.2",
    ]]
    found = kinds(f.__dict__ for f in grid_findings(["", "human", "MRCNN"], rows))
    assert "merged_cells" in found
    assert "whole column collapsed into one cell" in found["merged_cells"]["detail"]

    # Two values in a cell still need the column to say they don't belong.
    pair = [["Na4f2", "1.79 1.525"], ["Al3f2", "1.28"], ["K_3f2", "2.38"],
            ["Ca3f2", "1.705"]]
    found = kinds(f.__dict__ for f in grid_findings(["type", "r"], pair))
    assert "otherwise single-value numeric columns" in found["merged_cells"]["detail"]


def test_a_digit_grouped_decimal_is_one_value_not_a_collapsed_row():
    # The page prints 0.85745 as `0.857 45` and -14.556089 as `-14.556 089`.
    # Reading the thin space as a row boundary reported six merged_cells across
    # the corpus on values that were never merged.
    from pdf2md.table_audit import grid_findings

    rows = [["1H", "2.792 85"], ["2H", "0.857 44"], ["14N", "0.403 76"]]
    assert [f.kind for f in grid_findings(["Nuclide", "moment"], rows)] == []


def test_two_integers_in_a_cell_still_read_as_a_collapse():
    # The decimal point is what marks the grouping convention. Two bare
    # integers have no such convention behind them and stay suspicious.
    from pdf2md.table_audit import grid_findings

    rows = [["A", "31"], ["B", "45"], ["C", "58"], ["D", "227 229"]]
    assert "merged_cells" in [f.kind for f in grid_findings(["x", "y"], rows)]


def test_a_collapsed_column_of_negatives_is_caught_but_a_range_is_not():
    from pdf2md.table_audit import grid_findings

    # The engine renders the page's minus sign detached: `-3383.702155` comes
    # out as `- 3383.702155`. A lone `-` is not a number, so a cell holding a
    # whole collapsed column of them used to fail the all-numeric test and be
    # skipped -- s00214-006-0174-5 table 2 flattened ten elements and thirty
    # energies into one data row with no finding raised.
    header = ["Element", "Double-zeta", "Triple-zeta"]
    # Four values is where a lone cell stands on its own evidence; the real
    # table carried ten.
    rows = [["Y Zr Nb Mo", "- 3383.702155 - 3597.033816 - 3818.129477 - 4047.120095",
             "- 3383.715818 - 3597.048279 - 3818.145046 - 4047.136901"]]
    kinds = {f.kind for f in grid_findings(header, rows)}
    assert "merged_cells" in kinds

    # A range leads with its value, not a sign: `151 - 153` in an `exp. ref`
    # column cites references 151 to 153 and is one cell, not two rows.
    ref_rows = [["[emim]", "109", "151 - 153"], ["[bmim]", "38.5", "17"],
                ["[hmim]", "32.2", "129"], ["[omim]", "12.1", "130"]]
    assert "merged_cells" not in {
        f.kind for f in grid_findings(["ionic liquid", "lambda", "exp. ref"], ref_rows)
    }
