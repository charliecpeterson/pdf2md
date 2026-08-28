"""Chunks must stay bounded and point back to source, Markdown, and assets."""

from __future__ import annotations

import json

import pytest

from pdf2md.chunks import write_chunks
from pdf2md.schema import (
    Block,
    BlockType,
    CoverageFlag,
    CoverageReport,
    CoverageStatus,
    Digitization,
    Document,
    FigureRef,
    Section,
    SectionKind,
    TableData,
)
from pdf2md.structure import build_structure


def test_chunks_are_bounded_and_carry_retrieval_context(tmp_path):
    blocks = [
        Block("#/p", BlockType.PARAGRAPH, "alpha beta gamma delta epsilon", 1),
        Block("#/t", BlockType.TABLE, "", 1),
        Block("#/f", BlockType.FIGURE, "", 2),
        Block(
            "#/e",
            BlockType.EQUATION,
            "E = mc^2",
            2,
            extra={"crop_path": "assets/equation.png", "ocr": True},
        ),
    ]
    for block in blocks:
        block.coverage_status = CoverageStatus.EMITTED
    structure = build_structure(blocks, None, title="Doc", page_count=2)
    doc = Document(
        "a" * 64,
        "/source.pdf",
        "a" * 64,
        1,
        2,
        structure.root,
        blocks=blocks,
        tables=[TableData(
            "#/t",
            1,
            None,
            "| A | B |\n|---|---|\n| 1 | 2 |",
            candidate_path="data/tables/t.md",
            data_path="data/tables/t.csv",
            json_path="data/tables/t.json",
            normalized_data_path="data/tables/page_001_panels.csv",
            normalized_json_path="data/tables/page_001_panels.json",
        )],
        figures=[FigureRef(
            "#/f",
            2,
            None,
            caption="Measured curve",
            asset_path="assets/figure.png",
            data_path="data/figure.csv",
            code_path="code/figure.py",
            digitization=Digitization(
                series=[[(0, 1), (1, 2)]],
                method="vector-path",
                confidence=1.0,
                note="exact fixture",
            ),
        )],
    )
    doc.coverage = CoverageReport(
        doc.doc_id,
        total_blocks=4,
        emitted=4,
        cropped=0,
        flagged=0,
        dropped=0,
        flags=[CoverageFlag("#/e", 2, "verify equation", "marker")],
    )

    write_chunks(
        tmp_path,
        doc,
        [tmp_path / "document.md"],
        {2: "assets/page_002.png"},
        max_chars=50,
    )

    chunks = [json.loads(line) for line in (tmp_path / "chunks.jsonl").read_text().splitlines()]
    assert [chunk["id"] for chunk in chunks] == [
        f"chunk-{index:06d}" for index in range(1, len(chunks) + 1)
    ]
    assert all(len(chunk["text"]) <= 50 for chunk in chunks)
    assert all(chunk["markdown"] == "document.md" for chunk in chunks)
    assert any("| A | B |" in chunk["text"] for chunk in chunks)
    table_chunk = next(chunk for chunk in chunks if "#/t" in chunk["block_ids"])
    assert table_chunk["assets"] == [
        "data/tables/t.md",
        "data/tables/t.csv",
        "data/tables/t.json",
        "data/tables/page_001_panels.csv",
        "data/tables/page_001_panels.json",
    ]
    figure_chunk = next(chunk for chunk in chunks if "#/f" in chunk["block_ids"])
    assert "Chart data, series 1" in figure_chunk["text"]
    assert figure_chunk["assets"] == [
        "assets/figure.png",
        "data/figure.csv",
        "code/figure.py",
    ]
    equation_chunk = next(chunk for chunk in chunks if "#/e" in chunk["block_ids"])
    assert equation_chunk["needs_review"]
    assert equation_chunk["review_dispositions"] == ["action_required"]
    assert equation_chunk["assets"] == ["assets/equation.png", "assets/page_002.png"]
    assert equation_chunk["source_pages"] == ["../source.pdf#page=2"]


def test_chunks_skip_page_furniture_and_validate_limit(tmp_path):
    blocks = [Block("#/h", BlockType.PAGE_HEADER, "running title", 1)]
    structure = build_structure(blocks, None, title="Doc", page_count=1)
    doc = Document("a" * 64, "/source.pdf", "a" * 64, 1, 1, structure.root, blocks=blocks)

    path = write_chunks(tmp_path, doc, [tmp_path / "document.md"], {})
    assert path.read_text() == ""

    with pytest.raises(ValueError, match="max_chars must be positive"):
        write_chunks(tmp_path, doc, [tmp_path / "document.md"], {}, max_chars=0)


def test_chunks_do_not_merge_blocks_across_source_pages(tmp_path):
    blocks = [
        Block("#/p1", BlockType.PARAGRAPH, "first page", 1),
        Block("#/p2", BlockType.PARAGRAPH, "second page", 2),
    ]
    structure = build_structure(blocks, None, title="Doc", page_count=2)
    doc = Document("a" * 64, "/source.pdf", "a" * 64, 1, 2, structure.root, blocks=blocks)

    write_chunks(tmp_path, doc, [tmp_path / "document.md"], {}, max_chars=1000)

    chunks = [json.loads(line) for line in (tmp_path / "chunks.jsonl").read_text().splitlines()]
    assert [chunk["pages"] for chunk in chunks] == [[1], [2]]
    assert [chunk["source_pages"] for chunk in chunks] == [
        ["../source.pdf#page=1"],
        ["../source.pdf#page=2"],
    ]


def test_chunks_keep_image_only_figures_and_map_split_sections(tmp_path):
    front = Block("#/front", BlockType.PARAGRAPH, "Preface", 1)
    figure = Block("#/figure", BlockType.FIGURE, "", 2)
    root = Section(
        "root",
        "Book",
        0,
        SectionKind.FRONT_MATTER,
        1,
        block_ids=[front.id],
        children=[Section(
            "chapter-1",
            "Chapter 1",
            1,
            SectionKind.CHAPTER,
            2,
            block_ids=[figure.id],
        )],
    )
    doc = Document(
        "a" * 64,
        "/source.pdf",
        "a" * 64,
        1,
        2,
        root,
        blocks=[front, figure],
        figures=[FigureRef("#/figure", 2, None, asset_path="assets/figure.png")],
    )

    write_chunks(
        tmp_path,
        doc,
        [tmp_path / "00_front.md", tmp_path / "01_chapter-1.md", tmp_path / "index.md"],
        {},
    )

    chunks = [json.loads(line) for line in (tmp_path / "chunks.jsonl").read_text().splitlines()]
    assert [chunk["markdown"] for chunk in chunks] == ["00_front.md", "01_chapter-1.md"]
    assert chunks[1]["text"] == "[Image-only figure on page 2; inspect the linked asset.]"
    assert chunks[1]["assets"] == ["assets/figure.png"]


def test_chunks_prefer_exact_emission_mapping_for_nested_chapter_files(tmp_path):
    blocks = [
        Block("part", BlockType.HEADING, "Part I", 1),
        Block("chapter", BlockType.PARAGRAPH, "chapter text", 2),
    ]
    root = Section(
        "root", "Book", 0, SectionKind.FRONT_MATTER, 1,
        children=[Section(
            "part-section", "Part I", 1, SectionKind.PART, 1,
            block_ids=["part"],
            children=[Section(
                "chapter-section", "Chapter", 2, SectionKind.CHAPTER, 2,
                block_ids=["chapter"],
            )],
        )],
    )
    doc = Document(
        "a" * 64, "/source.pdf", "a" * 64, 1, 2, root, blocks=blocks
    )

    write_chunks(
        tmp_path,
        doc,
        [tmp_path / "01_part-i.md", tmp_path / "02_chapter.md", tmp_path / "index.md"],
        {},
        emission_index={
            "part": {"markdown": "01_part-i.md"},
            "chapter": {"markdown": "02_chapter.md"},
        },
    )

    chunks = [json.loads(line) for line in (tmp_path / "chunks.jsonl").read_text().splitlines()]
    assert [chunk["markdown"] for chunk in chunks] == [
        "01_part-i.md", "02_chapter.md",
    ]


def test_chunks_do_not_expose_rejected_chart_numbers(tmp_path):
    block = Block("#/figure", BlockType.FIGURE, "", 1)
    structure = build_structure([block], None, title="Doc", page_count=1)
    doc = Document(
        "a" * 64,
        "/source.pdf",
        "a" * 64,
        1,
        1,
        structure.root,
        blocks=[block],
        figures=[FigureRef(
            block.id,
            1,
            None,
            caption="Estimated curve",
            asset_path="assets/figure.png",
            digitization=Digitization(
                series=[[(123.0, 456.0)]],
                method="vlm-estimated",
                confidence=0.3,
                note="uncertain",
            ),
        )],
    )

    write_chunks(tmp_path, doc, [tmp_path / "document.md"], {})

    chunk = json.loads((tmp_path / "chunks.jsonl").read_text())
    assert chunk["text"] == "Estimated curve"
    assert "123" not in chunk["text"] and "456" not in chunk["text"]
    assert chunk["assets"] == ["assets/figure.png"]


def test_chunk_bbox_unions_member_blocks(tmp_path):
    from pdf2md.schema import BBox

    blocks = [
        Block("#/b1", BlockType.PARAGRAPH, "first paragraph", 1,
              bbox=BBox(x0=100, y0=700, x1=300, y1=690)),
        Block("#/b2", BlockType.PARAGRAPH, "second paragraph", 1,
              bbox=BBox(x0=90, y0=650, x1=310, y1=640)),
        Block("#/b3", BlockType.PARAGRAPH, "no geometry here", 1),  # no bbox
    ]
    structure = build_structure(blocks, None, title="D", page_count=1)
    doc = Document("a" * 64, "/source.pdf", "a" * 64, 1, 1, structure.root, blocks=blocks)
    write_chunks(tmp_path, doc, [tmp_path / "document.md"], {})

    chunks = [json.loads(line) for line in
              (tmp_path / "chunks.jsonl").read_text().splitlines()]
    assert len(chunks) == 1
    # Same page + same section merges into one chunk whose bbox covers both blocks;
    # the geometry-less block neither contributes nor breaks the union.
    assert chunks[0]["bbox"] == {"x0": 90.0, "y0": 700.0, "x1": 310.0, "y1": 640.0}


def test_chunk_without_any_geometry_has_no_bbox(tmp_path):
    block = Block("#/b1", BlockType.PARAGRAPH, "text only", 1)
    structure = build_structure([block], None, title="D", page_count=1)
    doc = Document("a" * 64, "/source.pdf", "a" * 64, 1, 1, structure.root, blocks=[block])
    write_chunks(tmp_path, doc, [tmp_path / "document.md"], {})
    chunk = json.loads((tmp_path / "chunks.jsonl").read_text())
    assert "bbox" not in chunk
