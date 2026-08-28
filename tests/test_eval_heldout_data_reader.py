"""Held-out data-cell comparisons keep numeric errors separate from refusals."""

from __future__ import annotations

import importlib.util
import json
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).parent.parent
SPEC = importlib.util.spec_from_file_location(
    "eval_heldout_data_reader", ROOT / "scripts" / "eval_heldout_data_reader.py"
)
data_reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(data_reader)


def test_numeric_value_ignores_formatting_but_not_extra_digits():
    assert data_reader._numeric_value("$0.10860^b$") == Decimal("0.10860")
    assert data_reader._numeric_value("—0.02146") == Decimal("-0.02146")
    assert data_reader._numeric_value("29 .00286") == Decimal("29.00286")
    assert data_reader._numeric_value("0.198798") == Decimal("0.198798")
    assert data_reader._numeric_value("no value") is None


def test_outcome_has_exact_three_states():
    assert data_reader._outcome("0.10860b", None, "0.10860") == "agree"
    assert data_reader._outcome("-0.28567D-02", None, "-0.28567D-02") == "agree"
    assert data_reader._outcome("0.10868", None, "0.10860") == "disagree"
    assert data_reader._outcome("0.10860", "below_threshold", "0.10860") == (
        "tool_refused"
    )
    assert data_reader._outcome("$\\vdots$", None, "vertical_ellipsis", "placeholder") == (
        "agree"
    )
    assert data_reader._outcome("0.1", None, "vertical_ellipsis", "placeholder") == (
        "disagree"
    )


def test_fallback_uses_projection_only_after_reference_refusal():
    reference = ("1.2", 0.999, None)
    projection = ("1.3", 0.999, None)
    assert data_reader._fallback_observation(reference, projection) == (
        "1.2", 0.999, None, "reference"
    )
    assert data_reader._fallback_observation(
        ("1.2", 0.8, "reader_score_below_threshold"), projection
    ) == ("1.3", 0.999, None, "projection")


def test_heldout_data_corpus_pins_cross_document_result():
    corpus = json.loads((ROOT / "tests" / "heldout_data_reader_corpus.json").read_text())

    assert corpus["expected"]["primary"] == {
        "checked": 56,
        "agree": 56,
        "disagree": 0,
        "tool_refused": 0,
    }
    assert corpus["expected"]["fallback"] == {
        "checked": 56,
        "agree": 53,
        "disagree": 0,
        "tool_refused": 3,
    }
    assert corpus["expected"]["confirmed"] == corpus["expected"]["fallback"]
