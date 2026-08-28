"""The labelled-accuracy harness gates CI-style, so its fact-checking must be right.
(Output discovery is file I/O, exercised by the real run.)"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "eval_accuracy", Path(__file__).parent.parent / "scripts" / "eval_accuracy.py"
)
ea = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ea)


def test_check_doc_all_pass():
    md = "Attention Is All You Need\nScaled Dot-Product Attention and Multi-Head Attention."
    profile = {"encoding_legibility": 1.0, "confidence": "high", "ocr_pages": 0}
    label = {"must_contain": ["Attention Is All You Need", "Multi-Head Attention"],
             "must_not_contain": ["/a114"], "min_encoding_legibility": 0.99,
             "confidence": "high"}
    assert all(ok for ok, _ in ea.check_doc(md, profile, label))


def test_check_doc_catches_each_failure():
    md = "❆ ♣/a114❛❝ garbage"  # a font-decode regression
    profile = {"encoding_legibility": 0.2, "confidence": "low", "ocr_pages": 0}
    label = {"must_contain": ["clean prose"], "must_not_contain": ["/a114"],
             "min_encoding_legibility": 0.95, "confidence": "high"}
    fails = [desc for ok, desc in ea.check_doc(md, profile, label) if not ok]
    assert any("clean prose" in d for d in fails)   # required text missing
    assert any("/a114" in d for d in fails)         # dingbat present
    assert any("legibility" in d for d in fails)    # below the floor
    assert any("confidence" in d for d in fails)    # wrong grade


def test_check_doc_scan_signals():
    profile = {"encoding_legibility": 1.0, "confidence": "medium", "ocr_pages": 50}
    label = {"confidence": "medium", "min_ocr_pages": 1}
    assert all(ok for ok, _ in ea.check_doc("", profile, label))


def test_check_mode_fails_when_labelled_source_is_missing(tmp_path, monkeypatch):
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps([{"source": "missing.pdf", "must_contain": ["text"]}]))
    monkeypatch.setattr(
        sys,
        "argv",
        ["eval_accuracy.py", str(tmp_path), "--labels", str(labels), "--strict"],
    )
    with pytest.raises(SystemExit, match="1"):
        ea.main()


def test_check_mode_fails_when_no_facts_run(tmp_path, monkeypatch):
    labels = tmp_path / "labels.json"
    labels.write_text("[]")
    monkeypatch.setattr(
        sys,
        "argv",
        ["eval_accuracy.py", str(tmp_path), "--labels", str(labels), "--strict"],
    )
    with pytest.raises(SystemExit, match="1"):
        ea.main()


def test_check_mode_fails_on_source_hash_mismatch(tmp_path, monkeypatch):
    version_dir = tmp_path / "document" / "v1"
    version_dir.mkdir(parents=True)
    (version_dir / "document.md").write_text("expected text")
    (version_dir / "profile.json").write_text(json.dumps({
        "source": "document.pdf",
        "source_sha256": "different",
        "encoding_legibility": 1.0,
    }))
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps([{
        "source": "document.pdf",
        "source_sha256": "expected",
        "must_contain": ["expected text"],
    }]))
    monkeypatch.setattr(
        sys,
        "argv",
        ["eval_accuracy.py", str(tmp_path), "--labels", str(labels), "--strict"],
    )

    with pytest.raises(SystemExit, match="1"):
        ea.main()
