"""Small pure-logic units: tables, cache, coverage, metadata."""

from __future__ import annotations

import hashlib

from pdf2md.cache import content_hash, latest_version, next_version
from pdf2md.coverage import build_report
from pdf2md.schema import Block, BlockType, CoverageStatus, TableData
from pdf2md.tables import render_table


def test_table_strips_caption_prefix():
    t = TableData("#/tables/0", 1, None, gfm="Table 1: x\n\n| a |\n|---|\n| 1 |")
    assert render_table(t).startswith("| a |")


def test_table_html_fallback_for_spanning_cells():
    t = TableData("#/tables/0", 1, None, gfm="ignored", html="<table></table>", has_spanning_cells=True)
    assert render_table(t) == "<table></table>"


def test_content_hash(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello")
    assert content_hash(p) == hashlib.sha256(b"hello").hexdigest()


def test_versioning(tmp_path):
    dd = tmp_path / "doc"
    assert latest_version(dd) is None
    assert next_version(dd) == 1
    (dd / "v1").mkdir(parents=True)
    (dd / "v2").mkdir()
    assert latest_version(dd) == 2
    assert next_version(dd) == 3


def test_coverage_report_counts_and_lossless():
    blocks = [
        Block("a", BlockType.PARAGRAPH, "x", 1, coverage_status=CoverageStatus.EMITTED),
        Block("b", BlockType.FIGURE, "", 1, coverage_status=CoverageStatus.CROPPED),
        Block("c", BlockType.EQUATION, "", 1, coverage_status=CoverageStatus.FLAGGED),
        Block("d", BlockType.PARAGRAPH, "", 1, coverage_status=CoverageStatus.DROPPED),
    ]
    report = build_report("doc", blocks, [])
    assert (report.total_blocks, report.emitted, report.cropped, report.flagged, report.dropped) == (4, 1, 1, 1, 1)
    assert report.lossless is True


def test_metadata_heuristic(monkeypatch):
    import pdf2md.metadata as m

    monkeypatch.setattr(m, "_embedded", lambda _p: {})
    blocks = [
        Block("#/texts/0", BlockType.HEADING, "My Great Paper", 1),
        Block("#/texts/1", BlockType.PARAGRAPH, "doi:10.1234/abc published in 2021", 1),
    ]
    meta = m.extract_metadata("ignored.pdf", blocks)
    assert meta["title"] == "My Great Paper"
    assert meta["doi"] == "10.1234/abc"
    assert meta["year"] == "2021"
