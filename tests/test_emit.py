from __future__ import annotations

from pdf2md.coverage import build_report
from pdf2md.emit import emit_document
from pdf2md.schema import CoverageStatus
from pdf2md.structure import build_structure


def _emit(tmp_path, doc):
    structure = build_structure(doc.blocks, None, title="Doc", page_count=doc.page_count)
    meta = {"title": "Doc", "authors": ["A. Author"], "year": "2021", "doi": None}
    return emit_document(doc, structure, tmp_path, meta, {"docling": "2.93.0", "pdf2md": "0.1.0"})


def test_emit_structural_facts(tmp_path, sample_document):
    md_files, flags = _emit(tmp_path, sample_document)
    assert [p.name for p in md_files] == ["document.md"]
    text = md_files[0].read_text()

    assert "format_version: '0.1'" in text
    assert "# 1 Introduction" in text          # heading depth 1
    assert "## 1.1 Background" in text          # nested heading depth 2
    assert "<!-- page 1 -->" in text and "<!-- page 2 -->" in text
    assert "![Figure 1](assets/pictures_0_p2.png)" in text
    assert "| a | b |" in text                  # table, caption stripped
    assert "$$" in text and "E = mc^2" in text  # equation as LaTeX
    assert "[^fn1]: a footnote" in text         # footnote collected
    assert "[pdf2md:" in text                   # the empty block emits a marker


def test_emit_is_lossless(tmp_path, sample_document):
    _, flags = _emit(tmp_path, sample_document)
    report = build_report(sample_document.doc_id, sample_document.blocks, flags)
    assert report.lossless
    assert report.cropped == 1          # the figure
    assert report.dropped == 1          # the empty paragraph
    # every block was accounted for
    assert all(b.coverage_status != CoverageStatus.PENDING for b in sample_document.blocks)


def test_emit_snapshot(tmp_path, sample_document, snapshot):
    md_files, _ = _emit(tmp_path, sample_document)
    assert md_files[0].read_text() == snapshot
