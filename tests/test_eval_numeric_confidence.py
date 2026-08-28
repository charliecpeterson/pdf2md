"""Selective confidence curves keep coverage, errors, and rewrites separate."""

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
    "eval_numeric_confidence", SCRIPTS / "eval_numeric_confidence.py"
)
confidence = importlib.util.module_from_spec(SPEC)
try:
    SPEC.loader.exec_module(confidence)
finally:
    sys.path.pop(0)


def test_score_point_separates_corrections_from_wrong_replacements():
    records = [
        {
            "candidate_score": 0.99,
            "candidate_outcome": "agree",
            "candidate_agrees_primary": False,
            "primary_outcome": "disagree",
        },
        {
            "candidate_score": 0.98,
            "candidate_outcome": "disagree",
            "candidate_agrees_primary": False,
            "primary_outcome": "agree",
        },
        {
            "candidate_score": 0.97,
            "candidate_outcome": "tool_refused",
            "candidate_agrees_primary": False,
            "primary_outcome": "agree",
        },
    ]

    point = confidence._score_point(0.98, records, len(records))

    assert point["accepted"] == 2
    assert point["correct"] == 1
    assert point["wrong"] == 1
    assert point["proposals"] == 2
    assert point["corrections"] == 1
    assert point["regressions"] == 1


def test_zero_wrong_threshold_excludes_the_highest_training_error():
    records = [
        {
            "candidate_score": 0.91,
            "candidate_outcome": "disagree",
            "candidate_agrees_primary": False,
            "primary_outcome": "agree",
        },
        {
            "candidate_score": 0.95,
            "candidate_outcome": "agree",
            "candidate_agrees_primary": True,
            "primary_outcome": "agree",
        },
    ]

    threshold = confidence._zero_wrong_training_threshold(records)

    assert threshold > 0.91
    assert confidence._score_point(threshold, records, 2)["wrong"] == 0


def test_pinned_confidence_evaluation_preserves_heldout_failure():
    report = confidence.evaluate(ROOT, confidence.DEFAULT_SOURCES)

    assert confidence.check_corpus(ROOT, confidence.DEFAULT_CORPUS, report)
    fixed = report["promotion_gate"]["fixed_0_99"]
    assert report["natural_signals"]["reader_agreement"][0]["accepted"] == 1069
    assert report["natural_signals"]["reader_agreement"][0]["errors"] == 0
    assert report["pp_reader"]["roles"] == {
        "heldout_clean_control": 56,
        "natural_primary_error": 14,
    }
    assert fixed["accepted"] == 54
    assert (fixed["corrections"], fixed["regressions"]) == (2, 0)
    assert fixed["wrong_replacement_rate_upper_95"] == 0.65761977
    assert report["pp_reader"]["learned_threshold_heldout_wrong"] == 1
    assert report["glyph_similarity"]["jackknife_stability"] == {
        "available": 8,
        "correct": 6,
        "wrong": 2,
    }
    assert report["promotion_gate"]["status"] == "not_defined"


def test_confidence_evaluation_rejects_artifact_hash_drift(tmp_path):
    sources = json.loads(confidence.DEFAULT_SOURCES.read_text())
    sources["artifacts"]["natural_report"]["sha256"] = "0" * 64
    sources_path = tmp_path / "sources.json"
    sources_path.write_text(json.dumps(sources))

    with pytest.raises(ValueError, match="natural_report"):
        confidence.evaluate(ROOT, sources_path)
