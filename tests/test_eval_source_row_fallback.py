"""The integrated crop fallback preserves old candidates and pins new coverage."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parent.parent
SPEC = importlib.util.spec_from_file_location(
    "eval_source_row_fallback", ROOT / "scripts" / "eval_source_row_fallback.py"
)
fallback = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fallback)


def test_prior_outcome_separates_changes_from_refusals():
    old = {"candidate_value": "1.20"}

    assert fallback._prior_outcome(old, {"candidate_value": "1.20"}) == "agree"
    assert fallback._prior_outcome(old, {"candidate_value": "1.2"}) == "disagree"
    assert fallback._prior_outcome(old, {"candidate_value": None}) == "tool_refused"
    assert fallback._prior_outcome(old, None) == "tool_refused"


def test_source_row_fallback_corpus_pins_preservation_and_coverage():
    corpus = json.loads((ROOT / "tests" / "source_row_fallback_corpus.json").read_text())

    assert corpus["expected"]["prior_candidates"] == {
        "checked": 610,
        "agree": 610,
        "disagree": 0,
        "tool_refused": 0,
    }
    assert corpus["expected"]["added_candidates"] == 137
