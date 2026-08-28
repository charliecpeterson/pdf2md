"""The natural-error third-reader result stays pinned by document and geometry."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "eval_natural_error_third_reader",
    SCRIPTS / "eval_natural_error_third_reader.py",
)
third_reader = importlib.util.module_from_spec(SPEC)
try:
    SPEC.loader.exec_module(third_reader)
finally:
    sys.path.pop(0)


def test_pinned_natural_error_third_reader_matches_independent_geometry_result():
    corpus = json.loads(
        (ROOT / "tests" / "non_fischer_third_reader_corpus.json").read_text()
    )

    report = third_reader.evaluate(ROOT, corpus)

    assert third_reader._checked_result(report) == corpus["expected"]
    assert report["reader"]["model_name"] == "PP-OCRv6_medium_rec"
    assert report["fixed_threshold_reader"] == {
        "checked": 14,
        "agree": 2,
        "disagree": 0,
        "tool_refused": 12,
    }
    assert report["preserved_cascade"]["corrections"] == 0


def test_third_reader_rejects_artifact_hash_drift():
    corpus = json.loads(
        (ROOT / "tests" / "non_fischer_third_reader_corpus.json").read_text()
    )
    corpus["artifacts"]["reader_run"]["sha256"] = "0" * 64

    with pytest.raises(ValueError, match="reader_run"):
        third_reader.evaluate(ROOT, corpus)
