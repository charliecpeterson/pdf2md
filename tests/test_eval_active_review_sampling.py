"""Active review ranking stays label-blind and its pinned comparison stays reproducible."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "eval_active_review_sampling", SCRIPTS / "eval_active_review_sampling.py"
)
sampling = importlib.util.module_from_spec(SPEC)
try:
    SPEC.loader.exec_module(sampling)
finally:
    sys.path.pop(0)


def _record(identifier: str, outcome: str, **overrides):
    record = {
        "id": identifier,
        "source_sha256": f"source-{identifier}",
        "block_id": "#/tables/1",
        "row": 1,
        "column": 1,
        "table_family": "plain",
        "typography": "born_digital",
        "confidence": "high",
        "reader_outcome": "agree",
        "reader_geometry": "mapped",
        "reader_refusal": None,
        "resolution_basis": "independent_reader_agreement",
        "primary_value": "1.0",
        "reader_value": "1.0",
        "primary_outcome": outcome,
    }
    record.update(overrides)
    return record


def test_active_order_does_not_read_review_outcomes():
    records = [
        _record("a", "agree"),
        _record("b", "disagree", confidence="low"),
        _record("c", "tool_refused", reader_outcome="tool_refused"),
    ]
    inverted = [
        {
            **record,
            "primary_outcome": (
                "disagree" if record["primary_outcome"] == "agree" else "agree"
            ),
        }
        for record in records
    ]

    original_order = [record["id"] for record in sampling._active_order(records)]
    inverted_order = [record["id"] for record in sampling._active_order(inverted)]

    assert original_order == inverted_order


def test_risk_components_prioritize_malformed_disagreement():
    components = sampling._risk_components(_record(
        "a",
        "agree",
        confidence="not_applicable",
        reader_outcome="disagree",
        reader_geometry="refused",
        reader_refusal="grid_alignment_failed",
        resolution_basis="unresolved_primary_retained",
        primary_value="not-a-number",
        reader_value="also-not-a-number",
    ))

    assert components == {
        "confidence": 9,
        "reader_outcome": 6,
        "geometry": 4,
        "refusal_reason": 5,
        "resolver_conflict": 2,
        "primary_syntax": 3,
        "reader_syntax": 3,
    }


def test_pinned_active_review_comparison():
    report = sampling.evaluate(ROOT, sampling.DEFAULT_SOURCES)

    assert sampling.check_corpus(ROOT, sampling.DEFAULT_CORPUS, report)
    assert report["active"]["curve"][:3] == [
        {
            "budget": 10,
            "errors_found": 8,
            "error_documents_found": 2,
            "errors_per_100_reviews": 80.0,
        },
        {
            "budget": 20,
            "errors_found": 9,
            "error_documents_found": 2,
            "errors_per_100_reviews": 45.0,
        },
        {
            "budget": 40,
            "errors_found": 11,
            "error_documents_found": 2,
            "errors_per_100_reviews": 27.5,
        },
    ]
    assert report["conclusion"]["promotion"] == (
        "retain_confidence_stratified_default_pending_heldout_evaluation"
    )
