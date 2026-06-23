"""The labelled-accuracy harness gates CI-style, so its fact-checking must be right.
(Output discovery is file I/O, exercised by the real run.)"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "eval_accuracy", Path(__file__).parent.parent / "scripts" / "eval_accuracy.py"
)
ea = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ea)


def test_check_doc_all_pass():
    md = "Attention Is All You Need\nScaled Dot-Product Attention and Multi-Head Attention."
    profile = {"prose_legibility": 1.0, "confidence": "high", "ocr_pages": 0}
    label = {"must_contain": ["Attention Is All You Need", "Multi-Head Attention"],
             "must_not_contain": ["/a114"], "min_legibility": 0.99, "confidence": "high"}
    assert all(ok for ok, _ in ea.check_doc(md, profile, label))


def test_check_doc_catches_each_failure():
    md = "❆ ♣/a114❛❝ garbage"  # a font-decode regression
    profile = {"prose_legibility": 0.2, "confidence": "low", "ocr_pages": 0}
    label = {"must_contain": ["clean prose"], "must_not_contain": ["/a114"],
             "min_legibility": 0.95, "confidence": "high"}
    fails = [desc for ok, desc in ea.check_doc(md, profile, label) if not ok]
    assert any("clean prose" in d for d in fails)   # required text missing
    assert any("/a114" in d for d in fails)         # dingbat present
    assert any("legibility" in d for d in fails)    # below the floor
    assert any("confidence" in d for d in fails)    # wrong grade


def test_check_doc_scan_signals():
    profile = {"prose_legibility": 1.0, "confidence": "medium", "ocr_pages": 50}
    label = {"confidence": "medium", "min_ocr_pages": 1}
    assert all(ok for ok, _ in ea.check_doc("", profile, label))
