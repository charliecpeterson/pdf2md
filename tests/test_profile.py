"""The document profile: inventory from the converted doc and a coarse confidence
grade with reasons. Pure aggregation, testable from synthetic blocks."""

from __future__ import annotations

import json

from pdf2md.profile import _confidence, build_profile, write_profile, write_readme
from pdf2md.schema import (
    Block,
    BlockType,
    CoverageReport,
    Document,
    FigureRef,
    TableData,
)
from pdf2md.structure import build_structure


def test_confidence_clean_is_high():
    grade, reasons = _confidence(lossless=True, illegible=0, ocr_pages=0, pages=10,
                                 equations=5, image_backed=0)
    assert grade == "high" and "clean" in reasons[0]


def test_confidence_scanned_is_medium():
    grade, reasons = _confidence(True, 0, 8, 10, 0, 0)
    assert grade == "medium" and any("scanned" in r for r in reasons)


def test_confidence_many_illegible_is_low():
    grade, reasons = _confidence(True, 12, 0, 10, 0, 0)
    assert grade == "low" and any("illegible" in r for r in reasons)


def test_confidence_not_lossless_is_low():
    grade, _ = _confidence(False, 0, 0, 10, 0, 0)
    assert grade == "low"


def test_image_backed_equations_are_informational_not_a_downgrade():
    grade, reasons = _confidence(True, 0, 0, 10, 10, 7)
    assert grade == "high" and any("image-backed" in r for r in reasons)


def test_build_profile_inventory():
    blocks = [
        Block("#/p", BlockType.PARAGRAPH, "hello world", 1),
        Block("#/e", BlockType.EQUATION, "x", 2, extra={"crop_path": "a.png"}),
        Block("#/c", BlockType.CODE, "print(1)", 2),
        Block("#/o", BlockType.PARAGRAPH, "ocr text", 3, extra={"ocr": True}),
    ]
    structure = build_structure(blocks, None, title="D", page_count=3)
    doc = Document("x" * 16, "/x.pdf", "x" * 16, 1, 3, structure.root, blocks=blocks,
                   figures=[FigureRef("#/f", 1, None)],
                   tables=[TableData("#/t", 1, None, gfm="| a |")])
    doc.coverage = CoverageReport("x", total_blocks=4, emitted=4, cropped=0, flagged=0,
                                  dropped=0, illegible=0)
    p = build_profile(doc)
    assert p.pages == 3 and p.blocks == 4
    assert p.equations == 1 and p.equations_image_backed == 1
    assert p.code_blocks == 1 and p.figures == 1 and p.tables == 1
    assert p.ocr_pages == 1
    assert p.lossless and p.prose_legibility == 1.0
    assert p.confidence == "high"


def test_write_profile_and_readme(tmp_path):
    blocks = [Block("#/p", BlockType.PARAGRAPH, "hello", 1)]
    structure = build_structure(blocks, None, title="Doc", page_count=1)
    doc = Document("abcd" * 4, "/x/Manual.pdf", "abcd" * 4, 1, 1, structure.root, blocks=blocks)
    doc.coverage = CoverageReport("x", total_blocks=1, emitted=1, cropped=0, flagged=0,
                                  dropped=0, illegible=0)
    profile = build_profile(doc)
    md_files = [tmp_path / "index.md", tmp_path / "01_intro.md"]

    write_profile(tmp_path, doc, profile, md_files)
    write_readme(tmp_path, doc, {"title": "The Manual"}, profile, md_files)

    pj = json.loads((tmp_path / "profile.json").read_text())
    assert pj["confidence"] == "high" and pj["contents"] == "index.md"
    assert pj["files"] == ["index.md", "01_intro.md"] and pj["source"] == "Manual.pdf"
    readme = (tmp_path / "README.md").read_text()
    assert "## Confidence: high" in readme and "Where to start" in readme
    assert "The Manual" in readme and "index.md" in readme
