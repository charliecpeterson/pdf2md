"""docling-parse traps (exit 133) on some embedded images instead of raising, which
kills the interpreter and any batch around it. The probe turns that into a page number;
these pin how it reads the child's outcome and what the engine then does."""

from __future__ import annotations

import subprocess

import pytest

from pdf2md.engines import docling_probe
from pdf2md.engines.docling import _bitmap_content_skipped, _quality_evidence


def _child(monkeypatch, *, returncode: int, stdout: str = "", stderr: str = "") -> None:
    monkeypatch.setattr(
        docling_probe.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, returncode, stdout, stderr),
    )


def test_a_trap_after_page_632_names_page_633(monkeypatch, tmp_path):
    # SIGTRAP is -5; the last page printed parsed, so the next one is the culprit.
    _child(monkeypatch, returncode=-5, stdout="".join(f"{n}\n" for n in range(1, 633)))
    assert docling_probe.crashing_page(tmp_path / "x.pdf") == 633


def test_a_trap_before_any_page_names_page_one(monkeypatch, tmp_path):
    _child(monkeypatch, returncode=-11)
    assert docling_probe.crashing_page(tmp_path / "x.pdf") == 1


def test_a_clean_parse_reports_no_crash(monkeypatch, tmp_path):
    _child(monkeypatch, returncode=0, stdout="1\n2\n")
    assert docling_probe.crashing_page(tmp_path / "x.pdf") is None


def test_an_ordinary_error_is_left_for_the_conversion_to_report(monkeypatch, tmp_path):
    _child(monkeypatch, returncode=1, stdout="1\n", stderr="Traceback ... password required")
    assert docling_probe.crashing_page(tmp_path / "x.pdf") is None


def test_the_crash_page_is_kept_in_the_quality_evidence():
    class Report:
        parse_score = ocr_score = layout_score = float("nan")
        pages: dict = {}

    evidence = _quality_evidence(Report(), 633)
    assert "page 633" in evidence["bitmap_content_skipped"]
    assert "bitmap_content_skipped" not in _quality_evidence(Report())


def test_bitmap_skipping_is_scoped_to_the_conversion():
    backend = pytest.importorskip("docling.backend.docling_parse_backend")
    from docling_parse.pdf_parser import ContentLevel

    original = backend._make_docling_parse_page_content_config
    kwargs = {"create_words": True, "create_textlines": True}
    assert original(**kwargs).bitmaps_content_level == ContentLevel.COMPUTE_AND_MATERIALIZE
    with _bitmap_content_skipped():
        assert backend._make_docling_parse_page_content_config(**kwargs).bitmaps_content_level == ContentLevel.SKIP
    assert backend._make_docling_parse_page_content_config is original
