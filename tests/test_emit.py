from __future__ import annotations

from pdf2md.coverage import build_report
from pdf2md.emit import _file_units, _tidy_math, emit_document
from pdf2md.schema import FORMAT_VERSION, CoverageStatus
from pdf2md.structure import build_structure


def _emit(tmp_path, doc):
    structure = build_structure(doc.blocks, None, title="Doc", page_count=doc.page_count)
    meta = {"title": "Doc", "authors": ["A. Author"], "year": "2021", "doi": None}
    return emit_document(doc, structure, tmp_path, meta, {"docling": "2.93.0", "pdf2md": "0.1.0"})


def test_emit_records_the_exact_block_artifact_and_text(tmp_path):
    from pdf2md.schema import Block, BlockType, Document

    block = Block("#/p", BlockType.PARAGRAPH, "alpha 42", 1)
    structure = build_structure([block], None, title="Doc", page_count=1)
    doc = Document(
        "a" * 64, "/source.pdf", "a" * 64, 1, 1, structure.root, blocks=[block]
    )
    emission_index = {}

    emit_document(
        doc,
        structure,
        tmp_path,
        {"title": "Doc"},
        {"test": "1"},
        emission_index=emission_index,
    )

    emission = emission_index["#/p"]
    assert emission["markdown"] == "document.md"
    assert emission["text"] == "alpha 42"
    assert emission["intentional_omission"] is False
    assert (tmp_path / "document.md").read_text()[
        emission["start"]:emission["end"]
    ] == "alpha 42"


def test_file_units_keep_part_openers_separate_from_chapters():
    from pdf2md.schema import Block, BlockType

    blocks = [
        Block("part-1", BlockType.HEADING, "Part I", 1),
        Block("chapter-1", BlockType.HEADING, "Chapter 1", 3),
        Block("body-1", BlockType.PARAGRAPH, "first", 4),
        Block("part-2", BlockType.HEADING, "Part II", 21),
        Block("chapter-2", BlockType.HEADING, "Chapter 2", 23),
        Block("body-2", BlockType.PARAGRAPH, "second", 24),
    ]
    bookmarks = [
        ("I First part", 0, 0),
        ("First chapter", 2, 1),
        ("II Second part", 20, 0),
        ("Second chapter", 22, 1),
    ]
    structure = build_structure(blocks, bookmarks, title="Book", page_count=60)

    units = _file_units(structure.root, structure.split_depth)

    assert [(section.title, ids) for section, ids in units] == [
        ("I First part", {"part-1"}),
        ("First chapter", {"chapter-1", "body-1"}),
        ("II Second part", {"part-2"}),
        ("Second chapter", {"chapter-2", "body-2"}),
    ]


def test_split_book_has_shallow_root_index_and_local_chapter_contents(tmp_path):
    from pdf2md.schema import Block, BlockType, Document

    blocks = [
        Block("contents", BlockType.HEADING, "Contents", 1),
        Block("part", BlockType.HEADING, "Part I", 3),
        Block("chapter-1", BlockType.HEADING, "1 First chapter", 5),
        Block("section-1", BlockType.HEADING, "1.1 Detail", 6),
        Block("body-1", BlockType.PARAGRAPH, "Read section 1.1.", 7),
        Block("chapter-2", BlockType.HEADING, "2 Second chapter", 20),
        Block("body-2", BlockType.PARAGRAPH, "Second body.", 21),
        Block("index", BlockType.HEADING, "Index", 50),
        Block("index-a", BlockType.HEADING, "A", 50),
    ]
    bookmarks = [
        ("Contents", 0, 0),
        ("Part I", 2, 0),
        ("1 First chapter", 4, 1),
        ("1.1 Detail", 5, 2),
        ("2 Second chapter", 19, 1),
        ("Index", 49, 0),
        ("A", 49, 1),
    ]
    structure = build_structure(blocks, bookmarks, title="Book", page_count=60)
    doc = Document(
        "a" * 64, "/source.pdf", "a" * 64, 1, 60, structure.root, blocks=blocks
    )

    files, _ = emit_document(
        doc,
        structure,
        tmp_path,
        {"title": "Book"},
        {"test": "1"},
    )

    index = (tmp_path / "index.md").read_text()
    chapter = next(path for path in files if "first-chapter" in path.name).read_text()
    assert "# Book: Contents" in index
    assert "  - [1 First chapter]" in index
    assert "1.1 Detail](" not in index
    assert index.count("[Index](") == 1
    assert "## In this file" in chapter
    assert "- [1.1 Detail](#11-detail)" in chapter
    assert "[section 1.1](#11-detail)" in chapter


def test_tidy_math_strips_spacing_blowups():
    # Docling pads trailing PDF whitespace with runaway \quad / control-spaces.
    trail = r"2 \pi _ { u } ^ { 2 } . \quad \ \ ( 9 ) \quad \ \ \ \ \ \ \ " + "\\"
    assert _tidy_math(trail) == r"2 \pi _ { u } ^ { 2 } .  \quad ( 9 )"

    # ...and pads lost alignment columns with repeated empty `& \quad` cells.
    cells = r"E = E ( X ) & \quad & \quad & \quad & \quad"
    assert _tidy_math(cells) == r"E = E ( X )"

    # Legitimate multi-column equations (single `& \quad`, real `\\`) are untouched.
    aligned = r"\Delta E & = E ( A ) & \quad \\ & - E ( B ) & \quad ( 5 )"
    assert _tidy_math(aligned) == aligned

    # A garbled equation with an unclosed brace (Docling misread `}` as `)`) gets
    # padded so KaTeX renders it instead of dumping the raw source.
    garbled = r"E ( \text {MR-AQC/CC) - E ( \text {x} )"
    fixed = _tidy_math(garbled)
    assert fixed.count("{") == fixed.count("}") == 2


def test_emit_writes_accepted_plot_data_and_code_as_linked_files(tmp_path):
    from pdf2md.schema import Block, BlockType, Digitization, Document, FigureRef

    block = Block("#/figures/1", BlockType.FIGURE, "", 1)
    structure = build_structure([block], None, title="Doc", page_count=1)
    figure = FigureRef(
        block.id,
        1,
        None,
        caption="Measured curve",
        asset_path="assets/figure_1.png",
        digitization=Digitization(
            series=[[(0.0, 1.0), (1.0, 2.0)]],
            method="vector-path",
            confidence=1.0,
            note="exact vector geometry",
        ),
    )
    doc = Document(
        "a" * 64,
        "/source.pdf",
        "a" * 64,
        1,
        1,
        structure.root,
        blocks=[block],
        figures=[figure],
    )

    _emit(tmp_path, doc)

    markdown = (tmp_path / "document.md").read_text()
    assert "[plot data (CSV)](data/figure_1.csv)" in markdown
    assert "[reproduction script (Python)](code/figure_1.py)" in markdown
    assert "```csv" not in markdown and "```python" not in markdown
    assert figure.data_path == "data/figure_1.csv"
    assert figure.code_path == "code/figure_1.py"
    assert (tmp_path / figure.data_path).read_text() == (
        "# x scale: linear\n# y scale: linear\n\n"
        "# series 1\nx,y\n0.0,1.0\n1.0,2.0\n"
    )
    compile((tmp_path / figure.code_path).read_text(), figure.code_path, "exec")


def test_emit_does_not_write_rejected_plot_artifacts(tmp_path):
    from pdf2md.schema import Block, BlockType, Digitization, Document, FigureRef

    block = Block("#/figures/1", BlockType.FIGURE, "", 1)
    structure = build_structure([block], None, title="Doc", page_count=1)
    figure = FigureRef(
        block.id,
        1,
        None,
        asset_path="assets/figure_1.png",
        data_path="data/stale.csv",
        code_path="code/stale.py",
        digitization=Digitization(
            series=[[(123.0, 456.0)]],
            method="vlm-estimated",
            confidence=0.3,
            note="uncertain",
        ),
    )
    doc = Document(
        "a" * 64,
        "/source.pdf",
        "a" * 64,
        1,
        1,
        structure.root,
        blocks=[block],
        figures=[figure],
    )

    _emit(tmp_path, doc)

    markdown = (tmp_path / "document.md").read_text()
    assert "data withheld" in markdown
    assert "123" not in markdown and "456" not in markdown
    assert figure.data_path == "" and figure.code_path == ""
    assert not (tmp_path / "data").exists() and not (tmp_path / "code").exists()


def test_emit_shows_figure_outcome_and_emits_associated_caption_once(tmp_path):
    from pdf2md.schema import BBox, Block, BlockType, Document, FigureRef
    from pdf2md.visual import associate_figure_captions

    figure_block = Block(
        "#/figures/1", BlockType.FIGURE, "", 1, BBox(10, 200, 190, 60)
    )
    caption_bbox = BBox(10, 55, 190, 35)
    caption_block = Block(
        "#/captions/1",
        BlockType.CAPTION,
        "FIG. 1. Incremental E<sub>corr</sub> values.",
        1,
        caption_bbox,
    )
    figure = FigureRef(
        figure_block.id,
        1,
        figure_block.bbox,
        caption="FIG. 1. Incremental Ecorr values.",
        caption_bbox=caption_bbox,
        asset_path="assets/figure.png",
        data_extraction_status="vector_archetype_unmatched",
        data_extraction_note="two vector plot frames detected",
    )
    blocks = [figure_block, caption_block]
    associate_figure_captions(blocks, [figure])
    structure = build_structure(blocks, None, title="Doc", page_count=1)
    doc = Document(
        "a" * 64,
        "/source.pdf",
        "a" * 64,
        1,
        1,
        structure.root,
        blocks=blocks,
        figures=[figure],
    )
    emission_index = {}

    emit_document(
        doc,
        structure,
        tmp_path,
        {"title": "Doc"},
        {"test": "1"},
        emission_index=emission_index,
    )

    markdown = (tmp_path / "document.md").read_text()
    assert "![FIG. 1](assets/figure.png)" in markdown
    assert markdown.count("Incremental E<sub>corr</sub> values.") == 1
    assert "plot data not extracted: vector_archetype_unmatched" in markdown
    assert "two vector plot frames detected" in markdown
    assert emission_index[caption_block.id]["intentional_omission"] is True
    assert "start" not in emission_index[caption_block.id]


def test_table_data_renders_even_when_block_mislabelled():
    from pdf2md.emit import _Ctx, _render_block
    from pdf2md.schema import Block, BlockType, CoverageStatus, TableData

    # Docling labels a TOC page 'other' but still parses cells; the data must render
    # rather than the block being dropped as empty.
    td = TableData(block_id="#/tables/2", page=21, bbox=None,
                   gfm="| Chapter | Page |\n|---|---|\n| 1 | 5 |")
    ctx = _Ctx(depth_of={}, tables={"#/tables/2": td}, figures={})
    blk = Block(id="#/tables/2", type=BlockType.OTHER, text="", page=21)
    text, status, _ = _render_block(blk, ctx, [])
    assert "| Chapter | Page |" in text and status == CoverageStatus.EMITTED


def test_failed_table_falls_back_to_image():
    from pdf2md.emit import _Ctx, _render_block
    from pdf2md.schema import Block, BlockType, CoverageStatus

    ctx = _Ctx(depth_of={}, tables={}, figures={})
    # A table Docling couldn't parse (type 'other', empty text) but with a crop:
    # emit the image instead of dropping the region.
    blk = Block(id="#/tables/2", type=BlockType.OTHER, text="", page=21,
                extra={"crop_path": "assets/tables_2_p21.png"})
    text, status, flag = _render_block(blk, ctx, [])
    assert "![table](assets/tables_2_p21.png)" in text
    assert status == CoverageStatus.CROPPED and flag is not None

    # On a scanned page the marker says the OCR text is unreliable.
    ocr = Block(id="#/tables/3", type=BlockType.OTHER, text="", page=5,
                extra={"crop_path": "assets/tables_3_p5.png", "ocr": True})
    text, status, _ = _render_block(ocr, ctx, [])
    assert "scanned page" in text and "![table](assets/tables_3_p5.png)" in text


def test_scanned_table_emits_crop_and_structured_candidate_artifacts(tmp_path):
    import csv
    import json

    from pdf2md.schema import Block, BlockType, Document, TableData

    block = Block(
        "#/mineru/30/table/120",
        BlockType.TABLE,
        "",
        30,
        extra={"crop_path": "assets/table.png", "ocr": True},
    )
    structure = build_structure([block], None, title="Doc", page_count=30)
    table = TableData(
        block.id,
        30,
        None,
        gfm=(
            "| RADIUS | 1S | 2S |\n"
            "|---|---|---|\n"
            "| 0.0001 | 0.0306 | . |\n"
            "| 0.0002 | 0.0611 | -0.0188 |"
        ),
    )
    doc = Document(
        "a" * 64,
        "/source.pdf",
        "a" * 64,
        1,
        30,
        structure.root,
        blocks=[block],
        tables=[table],
    )

    md_files, flags = _emit(tmp_path, doc)

    markdown = md_files[0].read_text()
    assert "![table](assets/table.png)" in markdown
    assert "structured OCR candidate" in markdown
    assert f"[Markdown]({table.candidate_path})" in markdown
    assert f"[CSV]({table.data_path})" in markdown
    assert f"[JSON]({table.json_path})" in markdown
    assert block.coverage_status == CoverageStatus.CROPPED
    flag = next(flag for flag in flags if flag.reason == "table candidate unverified")
    assert flag.disposition == "action_required"

    assert (tmp_path / table.candidate_path).read_text().startswith("| RADIUS | 1S | 2S |")
    with (tmp_path / table.data_path).open(newline="") as stream:
        assert list(csv.reader(stream)) == [
            ["RADIUS", "1S", "2S"],
            ["0.0001", "0.0306", "."],
            ["0.0002", "0.0611", "-0.0188"],
        ]
    record = json.loads((tmp_path / table.json_path).read_text())
    assert record["authority"] == "ocr_candidate"
    assert record["source_crop"] == "assets/table.png"
    assert record["rows"][2][2] == "-0.0188"


def test_scanned_table_without_crop_stays_flagged_candidate(tmp_path):
    from pdf2md.schema import Block, BlockType, Document, TableData

    block = Block("#/table", BlockType.TABLE, "", 1, extra={"ocr": True})
    structure = build_structure([block], None, title="Doc", page_count=1)
    table = TableData(block.id, 1, None, "| A |\n|---|\n| 1 |")
    doc = Document(
        "a" * 64,
        "/source.pdf",
        "a" * 64,
        1,
        1,
        structure.root,
        blocks=[block],
        tables=[table],
    )

    md_files, flags = _emit(tmp_path, doc)

    markdown = md_files[0].read_text()
    assert "scanned table crop unavailable" in markdown
    assert "Structured OCR candidate" in markdown
    assert "| A |" not in markdown
    assert block.coverage_status == CoverageStatus.FLAGGED
    assert [flag.reason for flag in flags] == ["scanned table crop unavailable"]


def test_repeated_table_fragments_emit_stitched_long_form_data(tmp_path):
    import csv
    import json

    from pdf2md.schema import Block, BlockType, Document, TableData

    blocks = [
        Block("#/table/1", BlockType.TABLE, "", 3, extra={"ocr": True, "crop_path": "assets/t1.png"}),
        Block("#/table/2", BlockType.TABLE, "", 3, extra={"ocr": True, "crop_path": "assets/t2.png"}),
    ]
    tables = [
        TableData(
            blocks[0].id,
            3,
            None,
            "\n".join([
                "| ATOM 29 | | | ATOM 30 | | |",
                "|---|---|---|---|---|---|",
                "| RADIUS | 1S | 2S | RADIUS | 1S | 2S |",
                "| 0.1 | 1.1 | 1.2 | 0.1 | 2.1 | 2.2 |",
                "| 0.2 | 1.3 | 1.4 | 0.2 | 2.3 | 2.4 |",
            ]),
        ),
        TableData(
            blocks[1].id,
            3,
            None,
            "\n".join([
                "| 0.3 | 1.5 | 1.6 | | | |",
                "|---|---|---|---|---|---|",
                "| | | | 0.3 | 2.5 | 2.6 |",
            ]),
        ),
    ]
    structure = build_structure(blocks, None, title="Doc", page_count=3)
    doc = Document(
        "a" * 64,
        "/source.pdf",
        "a" * 64,
        1,
        3,
        structure.root,
        blocks=blocks,
        tables=tables,
    )

    md_files, _ = _emit(tmp_path, doc)

    assert tables[0].normalized_data_path == "data/tables/page_003_panels.csv"
    assert tables[1].normalized_data_path == tables[0].normalized_data_path
    assert "normalized CSV" in md_files[0].read_text()
    with (tmp_path / tables[0].normalized_data_path).open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 12
    assert rows[4] == {
        "panel": "0",
        "title": "ATOM 29",
        "atomic_number": "",
        "symbol": "",
        "term": "",
        "configuration": "",
        "row_key": "0.3",
        "column": "1S",
        "value": "1.5",
        "raw_value": "1.5",
        "numeric_value": "1.5",
        "value_status": "numeric",
        "source_block_id": "#/table/2",
        "source_row": "0",
        "source_column": "1",
        "primary_value": "1.5",
        "reader_value": "",
        "best_value": "1.5",
        "confidence": "low",
        "resolution_basis": "single_reader_primary_retained",
        "validator_preference": "",
        "validator_basis": "",
        "verification_status": "candidate",
        "reader_refusal_reason": "",
    }
    normalized = json.loads((tmp_path / tables[0].normalized_json_path).read_text())
    assert normalized["schema_version"] == 5
    assert normalized["records"][4]["best_value"] == "1.5"
    assert normalized["authority"] == "ocr_candidate"
    assert normalized["source_blocks"] == ["#/table/1", "#/table/2"]
    assert normalized["checks"] == {"passed": True, "issues": [], "review_signals": []}
    assert normalized["panels"][1]["rows"][2]["cells"] == ["0.3", "2.5", "2.6"]
    assert normalized["panels"][1]["rows"][2]["source_row"] == 1
    sidecar = json.loads((tmp_path / tables[1].json_path).read_text())
    assert sidecar["normalized_json"] == "data/tables/page_003_panels.json"


def test_repeated_panel_refusals_do_not_reach_normalized_records(tmp_path):
    import csv
    import json

    from pdf2md.schema import Block, BlockType, Document, TableData

    block = Block("#/table", BlockType.TABLE, "", 3, extra={"ocr": True})
    grid = [
        ["ATOM 8", "", "", "", "ATOM 9", "", "", "", "ATOM 10", "", "", ""],
        ["RADIUS", "1S", "2S", "2P", "RADIUS", "1S", "2S", "2P",
         "RADIUS", "1S", "2S", "2P"],
        ["2.600", "0.0002", "-0.1967", "0.2836",
         "2.600", "0.0001", "-0.1344", "0.2233",
         "2.600", "-0.0906", "0.1754", ""],
        ["3.000", "-", "-0.1190", "0.2010",
         "3.000", "-0.0750", "0.1505", "3.000",
         "3.000", "-0.0468", "0.1128", ""],
        ["3.500", "-", "-0.0622", "0.1287",
         "3.500", "-0.0350", "0.0906", "3.500",
         "-0.0350", "-0.0199", "0.0641", ""],
    ]
    lines = ["| " + " | ".join(row) + " |" for row in grid]
    lines.insert(1, "|" + "|".join(["---"] * 12) + "|")
    table = TableData(block.id, 3, None, "\n".join(lines))
    structure = build_structure([block], None, title="Doc", page_count=3)
    doc = Document(
        "a" * 64, "/source.pdf", "a" * 64, 1, 3, structure.root,
        blocks=[block], tables=[table],
    )

    _emit(tmp_path, doc)

    with (tmp_path / table.normalized_data_path).open(newline="") as stream:
        records = list(csv.DictReader(stream))
    assert not any(
        record["panel"] == "1" and record["row_key"] == "3.000"
        for record in records
    )
    assert not any(record["panel"] == "2" for record in records)

    normalized = json.loads((tmp_path / table.normalized_json_path).read_text())
    assert normalized["panels"][1]["refused_rows"] == [
        {
            "source_block_id": block.id,
            "source_row": 3,
            "reason": "ambiguous_shifted_panel_boundary",
            "cells": ["3.000", "-0.0750", "0.1505", "3.000"],
        },
        {
            "source_block_id": block.id,
            "source_row": 4,
            "reason": "ambiguous_shifted_panel_boundary",
            "cells": ["3.500", "-0.0350", "0.0906", "3.500"],
        },
    ]
    assert normalized["checks"]["passed"] is False
    assert normalized["checks"]["issues"] == [
        {
            "kind": "panel_row_refused",
            "panel": 1,
            "source_block_id": block.id,
            "source_row": 3,
            "reason": "ambiguous_shifted_panel_boundary",
        },
        {
            "kind": "panel_row_refused",
            "panel": 1,
            "source_block_id": block.id,
            "source_row": 4,
            "reason": "ambiguous_shifted_panel_boundary",
        },
        {
            "kind": "panel_row_refused",
            "panel": 2,
            "source_block_id": block.id,
            "source_row": 2,
            "reason": "ambiguous_trailing_blank",
        },
        {
            "kind": "panel_row_refused",
            "panel": 2,
            "source_block_id": block.id,
            "source_row": 3,
            "reason": "ambiguous_shifted_panel_boundary",
        },
        {
            "kind": "panel_row_refused",
            "panel": 2,
            "source_block_id": block.id,
            "source_row": 4,
            "reason": "ambiguous_shifted_panel_boundary",
        },
    ]


def test_numeric_table_spike_is_review_only(tmp_path):
    import json

    from pdf2md.schema import Block, BlockType, Document, TableData

    block = Block("#/table", BlockType.TABLE, "", 1, extra={"ocr": True})
    rows = [
        "| LEFT | | RIGHT | |",
        "|---|---|---|---|",
        "| R | X | R | X |",
        "| 0 | 0.0 | 0 | 0.0 |",
        "| 1 | 1.0 | 1 | 1.0 |",
        "| 2 | 20.0 | 2 | 2.0 |",
        "| 3 | 3.0 | 3 | 3.0 |",
        "| 4 | 4.0 | 4 | 4.0 |",
    ]
    table = TableData(block.id, 1, None, "\n".join(rows))
    structure = build_structure([block], None, title="Doc", page_count=1)
    doc = Document(
        "a" * 64,
        "/source.pdf",
        "a" * 64,
        1,
        1,
        structure.root,
        blocks=[block],
        tables=[table],
    )

    _emit(tmp_path, doc)

    normalized = json.loads((tmp_path / table.normalized_json_path).read_text())
    assert normalized["checks"]["passed"] is True
    assert normalized["checks"]["issues"] == []
    assert normalized["checks"]["review_signals"] == [{
        "kind": "local_numeric_spike",
        "panel": 0,
        "row": 2,
        "row_key": "2",
        "column": "X",
        "value": "20.0",
        "score": 18.0,
        "source_block_id": block.id,
    }]


def test_numeric_spike_check_skips_noncontinuous_orbital_rows(tmp_path):
    import json

    from pdf2md.schema import Block, BlockType, Document, TableData

    block = Block("#/table", BlockType.TABLE, "", 1, extra={"ocr": True})
    table = TableData(block.id, 1, None, "\n".join([
        "| 44 | RU | 5D | (4D)6 | | | | | | 45 | RH | 4F | (4D)7 | | | | | |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        "| NL | E | | A | S | R | R**2 | 1/R | 1/R**3 | NL | E | | A | S | R | R**2 | 1/R | 1/R**3 |",
        "| 1S | 1 | | 1 | 1 | 1 | 1 | 1 | 1 | 1S | 1 | | 1 | 1 | 1 | 1 | 1 | 1 |",
        "| 2S | 2 | | 2 | 2 | 2 | 2 | 2 | 2 | 2S | 2 | | 2 | 2 | 2 | 2 | 2 | 2 |",
        "| 2P | 30 | | 30 | 30 | 30 | 30 | 30 | 30 | 2P | 30 | | 30 | 30 | 30 | 30 | 30 | 30 |",
        "| 3S | 4 | | 4 | 4 | 4 | 4 | 4 | 4 | 3S | 4 | | 4 | 4 | 4 | 4 | 4 | 4 |",
        "| 3P | 5 | | 5 | 5 | 5 | 5 | 5 | 5 | 3P | 5 | | 5 | 5 | 5 | 5 | 5 | 5 |",
    ]))
    structure = build_structure([block], None, title="Doc", page_count=1)
    doc = Document(
        "a" * 64, "/source.pdf", "a" * 64, 1, 1, structure.root,
        blocks=[block], tables=[table],
    )

    _emit(tmp_path, doc)

    normalized = json.loads((tmp_path / table.normalized_json_path).read_text())
    assert not any(
        signal["kind"] == "local_numeric_spike"
        for signal in normalized["checks"]["review_signals"]
    )


def test_balance_delims():
    from pdf2md.emit import _balance_delims

    # one \left, two \right won't compile in KaTeX -> drop the sizing commands.
    bad = r"\left\langle a \right| b \right\rangle"
    assert _balance_delims(bad) == r"\langle a | b \rangle"
    # a balanced pair is left untouched.
    ok = r"\left( a + b \right)"
    assert _balance_delims(ok) == ok


def test_low_confidence_equation_uses_image_and_hint():
    from pdf2md.emit import _Ctx, _render_block
    from pdf2md.schema import Block, BlockType, CoverageStatus

    ctx = _Ctx(depth_of={}, tables={}, figures={})

    # Suspect extraction with an ordered, clean text layer: image is authoritative,
    # the text-layer reading rides along as the hint.
    eq = Block(id="#/texts/9", type=BlockType.EQUATION, text="E ( garbled )", page=3,
               confidence=0.6, extra={"crop_path": "assets/eq_p3.png",
                                      "text_layer": "E(MR-AQCC/cc-pVTZ) (4)", "ordered": True})
    text, status, flag = _render_block(eq, ctx, [])
    assert "![equation](assets/eq_p3.png)" in text
    assert "E(MR-AQCC/cc-pVTZ) (4)" in text          # the reading hint, not $$ LaTeX
    assert status == CoverageStatus.CROPPED and flag is not None

    # Scrambled text layer (ordered=False): fall back to the vision LaTeX as the hint.
    eq2 = Block(id="#/texts/10", type=BlockType.EQUATION, text="E _ { n } = E _ { CBS }", page=3,
                confidence=0.0, extra={"crop_path": "assets/eq2_p3.png",
                                       "text_layer": "E E n CBS scrambled", "ordered": False})
    text2, _, _ = _render_block(eq2, ctx, [])
    assert "![equation](assets/eq2_p3.png)" in text2 and "$$" in text2  # LaTeX hint, not soup
    assert "scrambled" not in text2


def test_empty_equation_with_crop_emits_image_not_empty_marker():
    from pdf2md.emit import _Ctx, _render_block
    from pdf2md.schema import Block, BlockType, CoverageStatus

    ctx = _Ctx(depth_of={}, tables={}, figures={})

    # --no-formula leaves an equation with no LaTeX at all, but its region was still cropped.
    # The crop is the authoritative record, so it must be emitted — not dropped as "empty".
    eq = Block(id="#/texts/50", type=BlockType.EQUATION, text="", page=3,
               extra={"crop_path": "assets/texts_50_p3.png"})
    text, status, flag = _render_block(eq, ctx, [])
    assert "![equation](assets/texts_50_p3.png)" in text
    assert "empty equation block" not in text and "$$" not in text  # no crop-less "empty", no bare $$
    assert status == CoverageStatus.CROPPED and flag is not None

    # A genuinely empty equation with no crop stays an honest visible marker.
    bare = Block(id="#/texts/178", type=BlockType.EQUATION, text="", page=8)
    t2, s2, _ = _render_block(bare, ctx, [])
    assert "empty equation block" in t2 and s2 == CoverageStatus.DROPPED


def test_emit_structural_facts(tmp_path, sample_document):
    md_files, flags = _emit(tmp_path, sample_document)
    assert [p.name for p in md_files] == ["document.md"]
    text = md_files[0].read_text()

    assert f"format_version: '{FORMAT_VERSION}'" in text
    assert "engine_versions:" in text and "\nengine:" not in text
    assert "# 1 Introduction" in text          # heading depth 1
    assert "## 1.1 Background" in text          # nested heading depth 2
    assert "<!-- page 1 -->" in text and "<!-- page 2 -->" in text
    assert "![Figure 1](assets/pictures_0_p2.png)" in text
    assert "| a | b |" in text                  # table, caption stripped
    assert "$$" in text and "E = mc^2" in text  # equation as LaTeX
    assert "[^fn1]: a footnote" in text         # footnote collected
    assert "[pdf2md:" in text                   # the empty block emits a marker


def test_emit_accounts_for_every_block(tmp_path, sample_document):
    _, flags = _emit(tmp_path, sample_document)
    report = build_report(sample_document.doc_id, sample_document.blocks, flags)
    assert report.accounted_for
    assert not report.complete and report.needs_review
    assert report.cropped == 1          # the figure
    assert report.dropped == 1          # the empty paragraph
    # every block was accounted for
    assert all(b.coverage_status != CoverageStatus.PENDING for b in sample_document.blocks)


def test_formula_option_separates_intentional_crop_from_suspect_extraction():
    from pdf2md.emit import _Ctx, _render_block
    from pdf2md.schema import Block, BlockType

    intentional = Block(
        "#/intentional", BlockType.EQUATION, "", 1,
        extra={"crop_path": "assets/intentional.png"},
    )
    source_ctx = _Ctx(
        depth_of={}, tables={}, figures={}, formula_enrichment_enabled=False,
    )
    _, _, source_flag = _render_block(intentional, source_ctx, [])

    suspect = Block(
        "#/suspect", BlockType.EQUATION, "bad latex", 1, confidence=0.0,
        extra={"crop_path": "assets/suspect.png"},
    )
    action_ctx = _Ctx(depth_of={}, tables={}, figures={})
    _, _, action_flag = _render_block(suspect, action_ctx, [])

    assert source_flag.disposition == "source_dependent"
    assert action_flag.disposition == "action_required"


def test_illegible_footnote_flagged_not_emitted():
    # A broken-font footnote is symbol-font garbage like any prose; it must be flagged,
    # not appended to the footnote list as readable text (the FOOTNOTE branch gates it).
    from pdf2md.emit import _Ctx, _render_block
    from pdf2md.schema import Block, BlockType

    ctx = _Ctx(depth_of={}, tables={}, figures={})
    fn = Block(id="#/fn", type=BlockType.FOOTNOTE, text="❆ ♣/a114❛❝", page=1)
    footnotes: list[str] = []
    text, status, flag = _render_block(fn, ctx, footnotes)
    assert status == CoverageStatus.FLAGGED and "illegible text layer" in text
    assert footnotes == []  # not passed off as readable


def test_page_raster_linked_only_at_scanned_page_anchors(tmp_path):
    # A scanned page links its full-page image so OCR prose can be verified; a
    # born-digital page (authoritative text layer) gets no such link.
    from pdf2md.schema import Block, BlockType, Document
    from pdf2md.structure import build_structure

    blocks = [
        Block(id="#/t0", type=BlockType.PARAGRAPH, text="born-digital page", page=1),
        Block(id="#/t1", type=BlockType.PARAGRAPH, text="scanned page text", page=2,
              extra={"ocr": True}),
    ]
    structure = build_structure(blocks, None, title="Doc", page_count=2)
    doc = Document(doc_id="abc123def4567890", source_path="/x/Doc.pdf",
                   source_sha256="abc123def4567890", version=1, page_count=2,
                   sections=structure.root, blocks=blocks, tables=[], figures=[])
    md_files, _ = emit_document(doc, structure, tmp_path, {"title": "Doc"},
                                {"docling": "2.93.0", "pdf2md": "0.1.0"},
                                page_rasters={2: "assets/page_002.png"})
    text = md_files[0].read_text()
    assert "[page 2 scan](assets/page_002.png)" in text  # scanned page linked
    assert "page 1 scan" not in text                     # born-digital page not linked


def test_ocr_disagreement_flags_but_keeps_heading_structure(tmp_path):
    # A scanned heading whose OCR re-reads disagree must keep its heading level (and TOC
    # entry) and still be flagged -- the flag composes with the render, never replaces it.
    from pdf2md.schema import Block, BlockType, Document
    from pdf2md.structure import build_structure

    blocks = [
        Block(id="#/h0", type=BlockType.HEADING, text="Section 3 Results", page=1,
              extra={"ocr": True, "ocr_disagreement": True}),
    ]
    structure = build_structure(blocks, None, title="Doc", page_count=1)
    doc = Document(doc_id="abc123def4567890", source_path="/x/Doc.pdf",
                   source_sha256="abc123def4567890", version=1, page_count=1,
                   sections=structure.root, blocks=blocks, tables=[], figures=[])
    md_files, flags = emit_document(doc, structure, tmp_path, {"title": "Doc"},
                                    {"docling": "2.93.0", "pdf2md": "0.1.0"})
    text = md_files[0].read_text()
    assert "# Section 3 Results" in text          # heading level preserved, not flattened
    assert "OCR uncertain" in text                # uncertainty surfaced
    assert "[source page 1](../source.pdf#page=1)" in text
    assert any("OCR uncertain" in f.reason for f in flags)  # counted as flagged, not silent


def test_emit_snapshot(tmp_path, sample_document, snapshot):
    md_files, _ = _emit(tmp_path, sample_document)
    assert md_files[0].read_text() == snapshot


def test_heading_plan_dedup_and_merge():
    from pdf2md.emit import _heading_plan
    from pdf2md.schema import Block, BlockType

    blocks = [
        Block("#/h0", BlockType.HEADING, "Part I Overview of GRASP2018", 1),
        Block("#/h1", BlockType.HEADING, "Chapter 1", 1),
        Block("#/h2", BlockType.HEADING, "GRASP2018", 1),
        Block("#/h3", BlockType.HEADING, "1.1 Relativistic calculations", 1),
    ]
    skip, text = _heading_plan(blocks, "I Overview of GRASP2018")
    assert "#/h0" in skip                            # restates the file title -> dropped
    assert text["#/h1"] == "Chapter 1: GRASP2018"    # bare label merged with its title
    assert "#/h2" in skip                            # the title was consumed by the merge
    assert "#/h3" not in skip and "#/h3" not in text  # a normal numbered section is left alone


def test_heading_plan_label_plus_title_dup_dropped():
    # When a "Part N" label is followed by a heading that restates the file title,
    # both are dropped (the file title already says it), not merged into a duplicate.
    from pdf2md.emit import _heading_plan
    from pdf2md.schema import Block, BlockType

    blocks = [
        Block("#/h0", BlockType.HEADING, "Part IV", 1),
        Block("#/h1", BlockType.HEADING, "Issues of convergence and non-default options", 1),
    ]
    skip, text = _heading_plan(blocks, "IV Issues of convergence and non-default options")
    assert "#/h0" in skip and "#/h1" in skip and "#/h0" not in text


def test_section_refs_linkified_outside_fences(tmp_path):
    from pdf2md.emit import _link_refs

    p = tmp_path / "doc.md"
    p.write_text("---\ntitle: x\n---\n\nSee section 9.2 here. Also section 1.1.\n\n"
                 "```\nrun and read section 9.2 now\n```\n\nAnd section 7 (no dot).\n")
    smap = {"9.2": ("09_x.md", "92-foo"), "1.1": ("doc.md", "11-bar")}
    _link_refs(p, smap)
    out = p.read_text()
    assert "[section 9.2](09_x.md#92-foo)" in out   # cross-file link
    assert "[section 1.1](#11-bar)" in out          # same-file -> bare anchor
    assert "run and read section 9.2 now" in out    # inside a code fence: left verbatim
    assert "And section 7 (no dot)." in out         # bare number: not linked (ambiguous)


def test_illegible_prose_flagged_not_silently_emitted(tmp_path):
    # A prose block still symbol-font garbage after enrich's refill must surface as
    # a visible marker + an `illegible` tally, not pass as readable text — the exact
    # blind spot that let GRASP report clean accounting while 67% was dingbats.
    from pdf2md.schema import Block, BlockType, Document
    from pdf2md.structure import build_structure

    g = Block(id="#/texts/0", type=BlockType.PARAGRAPH, text="❆ ♣/a114❛❝/a116✐❝❛❧", page=1)
    structure = build_structure([g], None, title="Doc", page_count=1)
    doc = Document(
        doc_id="abc123def456789a", source_path="/x/Doc.pdf", source_sha256="abc123def456789a",
        version=1, page_count=1, sections=structure.root, blocks=[g], tables=[], figures=[],
    )
    md_files, flags = emit_document(doc, structure, tmp_path, {"title": "Doc"},
                                    {"docling": "2.93.0", "pdf2md": "0.1.0"})
    text = md_files[0].read_text()

    assert "[pdf2md: illegible text layer]" in text
    assert "[source page 1](../source.pdf#page=1)" in text
    assert "❆" not in text                 # the garbage itself is not emitted as prose
    assert "illegible_blocks: 1" in text    # front-matter surfaces it
    assert g.coverage_status == CoverageStatus.FLAGGED
    report = build_report(doc.doc_id, doc.blocks, flags)
    assert report.illegible == 1 and report.accounted_for and not report.complete
