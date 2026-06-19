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


def test_build_html_spans_and_headers():
    from pdf2md.tables import GridCell, build_html

    cells = [
        GridCell("H", 0, 0, col_span=2, header=True),
        GridCell("a", 1, 0),
        GridCell("b", 1, 1),
    ]
    html = build_html(cells, 2, 2)
    assert '<th colspan="2">H</th>' in html
    assert "<tr><td>a</td><td>b</td></tr>" in html


def test_build_gfm_derives_header_from_flags():
    from pdf2md.tables import GridCell, build_gfm

    cells = [
        GridCell("A", 0, 0, header=True),
        GridCell("B", 0, 1, header=True),
        GridCell("1", 1, 0),
        GridCell("2", 1, 1),
    ]
    assert build_gfm(cells, 2, 2).splitlines() == ["| A | B |", "|---|---|", "| 1 | 2 |"]


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


def test_prune_keeps_newest(tmp_path, monkeypatch):
    from pdf2md.cache import prune

    monkeypatch.setenv("PDF2MD_OUT", str(tmp_path))
    dd = tmp_path / "docabc123def456"
    for v in (1, 2, 3):
        (dd / f"v{v}").mkdir(parents=True)

    removed = prune(keep=1)

    assert {p.name for p in removed} == {"v1", "v2"}
    assert (dd / "v3").exists()
    assert not (dd / "v1").exists() and not (dd / "v2").exists()


def test_prune_dry_run_removes_nothing(tmp_path, monkeypatch):
    from pdf2md.cache import prune

    monkeypatch.setenv("PDF2MD_OUT", str(tmp_path))
    dd = tmp_path / "docabc123def456"
    for v in (1, 2):
        (dd / f"v{v}").mkdir(parents=True)

    removed = prune(keep=1, dry_run=True)

    assert len(removed) == 1
    assert (dd / "v1").exists()  # untouched


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


def test_unglyph_maps_greek_names():
    from pdf2md.normalize import unglyph

    assert unglyph("/Delta1f H") == "Δf H"
    assert unglyph("( 2 /Pi1 )") == "( 2 Π )"
    assert unglyph("E _ { /Sigma1 }") == "E _ { Σ }"


def test_unglyph_leaves_non_glyphs_alone():
    from pdf2md.normalize import unglyph

    assert unglyph("assets/pictures_0.png") == "assets/pictures_0.png"  # pi-prefix, no match
    assert unglyph("</td><th>/Si</th>") == "</td><th>/Si</th>"  # html + silicon untouched
    assert unglyph("plain text") == "plain text"


def test_strip_orphan_combining():
    from pdf2md.normalize import strip_orphan_combining

    assert strip_orphan_combining("̸") == ""          # lone solidus overlay -> nothing
    assert strip_orphan_combining("a ̸b") == "a b"     # orphan after space dropped
    assert strip_orphan_combining("≠") == "≠"   # real base+mark (≠) kept
    assert strip_orphan_combining("plain") == "plain"


def test_assess_equation():
    from pdf2md.confidence import assess_equation

    # Garbled LaTeX (AQCC->AQC/CC, pVTZ->pVTEZ) vs a clean text layer: low score,
    # recoverable, and the clean text layer is handed back as the reading.
    conf, reading, recoverable = assess_equation(
        r"E ( \text {MR-AQC/CC/cc-pVTEZ) - E ( \text {CASPT} 2 / \text {cc-pVTEZ} ) \quad ( 4 )",
        "E(MR-AQCC/cc-pVTZ) − E(CASPT2/cc-pVTZ) (4)")
    assert conf < 0.85 and recoverable and reading == "E(MR-AQCC/cc-pVTZ) − E(CASPT2/cc-pVTZ) (4)"

    # Docling spaces out every glyph; once rejoined a faithful LaTeX scores 1.0.
    assert assess_equation(
        r"E ( M R - c c C A ) & = E _ { 0 } ( M R - c c C A )",
        "E(MR-ccCA) = E0(MR-ccCA)") == (1.0, None, False)

    # \exp / \max produce visible text; stripping them must not fake a mismatch on a
    # correct equation (this is what wrongly recovered Eq 2 and flattened its frac).
    conf, reading, _ = assess_equation(
        r"E ( l _ { \max } ) = E _ { C B S } + \frac { D } { ( l _ { \max } + 1 / 2 ) ^ { 4 } }",
        "E(lmax) = ECBS + D (lmax + 1/2)4")
    assert conf == 1.0 and reading is None

    # Low score, but the LaTeX has Δ terms the text layer dropped (pdfium omits the
    # unmapped symbol-font glyph): keep the LaTeX, surface the reading, don't recover.
    conf, reading, recoverable = assess_equation(
        r"T A E = & \Delta E ( S O C ) + E _ { M R - c c A } ( S i )",
        "TAE = E(SOC) + EMR-ccCA(Si) (7)")
    assert conf < 0.85 and reading is not None and not recoverable

    # Too few alphanumeric tokens to judge (symbol-heavy orbital config).
    assert assess_equation(r"[ \text {Core} ] 4 \sigma", "[Core]4σ") is None


def test_unsplit_numbers_protects_values():
    from pdf2md.scripts import apply_scripts

    # A digit raised inside a number is a misdetection: 191.4 must stay 191.4.
    scored = [("1", "sup"), ("9", None), ("1", None), (".", None), ("4", None)]
    assert apply_scripts("191.4", scored) == "191.4"
    # ...but a real trailing citation/exponent survives (191.4⁶⁹).
    scored = [(c, None) for c in "191.4"] + [("6", "sup"), ("9", "sup")]
    assert apply_scripts("191.469", scored) == "191.4<sup>69</sup>"
    # A left-superscript multiplicity (²A) is kept — the digit precedes a letter.
    assert apply_scripts("2A1", [("2", "sup"), ("A", None), ("1", "sub")]) == "<sup>2</sup>A<sub>1</sub>"


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
