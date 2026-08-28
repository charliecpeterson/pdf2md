"""Held-out review weights never see labels from the document they rank."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location(
    "eval_active_review_heldout", SCRIPTS / "eval_active_review_heldout.py"
)
heldout = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(heldout)
finally:
    sys.path.pop(0)


def _record(identifier: str, outcome: str, confidence: str = "high") -> dict:
    return {
        "id": identifier,
        "source_sha256": "heldout",
        "block_id": "#/table/1",
        "table_family": "plain",
        "typography": "born_digital",
        "confidence": confidence,
        "reader_outcome": "agree",
        "reader_geometry": "mapped",
        "reader_refusal": None,
        "resolution_basis": "independent_reader_agreement",
        "primary_value": "1.0",
        "reader_value": "1.0",
        "primary_outcome": outcome,
    }


def test_frozen_weight_order_does_not_read_heldout_outcomes():
    records = [
        _record("high", "agree"),
        _record("low", "disagree", confidence="low"),
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
    weights = {name: 1.0 for name in heldout._risk_components(records[0])}

    assert [record["id"] for record in heldout._weighted_order(records, weights)] == [
        record["id"] for record in heldout._weighted_order(inverted, weights)
    ]


def test_pinned_leave_one_document_out_result():
    report = heldout.evaluate(ROOT, heldout.DEFAULT_SOURCES)

    assert heldout.check_corpus(ROOT, heldout.DEFAULT_CORPUS, report)
    assert report["sampling_frame"] == {
        "cells": 2113,
        "documents": 31,
        "labelled_errors": 14,
        "heldout_folds": 3,
        "zero_error_training_controls": 28,
        "budgets_per_document": [1, 3, 5, 10, 20],
    }
    assert report["conclusion"]["promotion"] == "retain_confidence_stratified_default"
    assert all(fold["training_documents"] == 30 for fold in report["folds"])
