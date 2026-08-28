"""Held-out reader comparisons keep wrong reads separate from refusals."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parent.parent
SPEC = importlib.util.spec_from_file_location(
    "eval_heldout_key_reader", ROOT / "scripts" / "eval_heldout_key_reader.py"
)
key_reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(key_reader)


def test_value_outcome_has_exact_three_states():
    assert key_reader._value_outcome("1.20", None, "1.2") == "agree"
    assert key_reader._value_outcome("1.3", None, "1.2") == "disagree"
    assert key_reader._value_outcome("1.2", "below_threshold", "1.2") == (
        "tool_refused"
    )
    assert key_reader._value_outcome(None, "reader_empty", "1.2") == "tool_refused"


def test_heldout_reader_corpus_pins_cross_document_result():
    corpus = json.loads((ROOT / "tests" / "heldout_key_reader_corpus.json").read_text())

    assert corpus["expected"]["reference"] == {
        "checked": 106,
        "agree": 103,
        "disagree": 1,
        "tool_refused": 2,
    }
    assert corpus["expected"]["projection"] == {
        "checked": 106,
        "agree": 106,
        "disagree": 0,
        "tool_refused": 0,
    }
    assert corpus["expected"]["fallback_readers"] == {
        "projection": 3,
        "reference": 103,
    }
