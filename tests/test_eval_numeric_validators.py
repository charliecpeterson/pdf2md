"""Numeric consistency rules remain diagnostic unless stronger evidence agrees."""

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
    "eval_numeric_validators", SCRIPTS / "eval_numeric_validators.py"
)
validators = importlib.util.module_from_spec(SPEC)
try:
    SPEC.loader.exec_module(validators)
finally:
    sys.path.pop(0)


def test_pinned_adversarial_corpus_falsifies_automatic_rewrites():
    report = validators.evaluate(validators.DEFAULT_CASES)

    assert validators.check_corpus(ROOT, validators.DEFAULT_CORPUS, report)
    assert report["rules"]["continuity"]["proposed_rewrite"] == {
        "correction": 3,
        "regression": 6,
        "retained": 0,
        "unresolved": 9,
    }
    assert report["rules"]["combined"]["proposed_rewrite"] == {
        "correction": 4,
        "regression": 8,
        "retained": 3,
        "unresolved": 3,
    }
    assert report["numeric_equivalence_false_agreements"] == 2


def test_discontinuity_regresses_while_smooth_ocr_error_is_detected():
    report = validators.evaluate(validators.DEFAULT_CASES)
    records = {record["id"]: record for record in report["records"]}

    assert records["legitimate_discontinuity"]["rules"]["combined"] == {
        "choice": "reader",
        "basis": "local_continuity_reader",
        "outcome": "disagree",
        "detection": "false_positive",
        "rewrite": "regression",
    }
    assert records["leading_digit_ocr_error"]["rules"]["combined"] == {
        "choice": "reader",
        "basis": "local_continuity_reader",
        "outcome": "agree",
        "detection": "true_positive",
        "rewrite": "correction",
    }


def test_numeric_identifier_semantics_do_not_use_numeric_equivalence():
    report = validators.evaluate(validators.DEFAULT_CASES)
    identifiers = [
        record
        for record in report["records"]
        if record["class"] == "numeric_looking_identifier"
    ]

    assert len(identifiers) == 2
    assert all(record["numeric_equivalence_false_agreement"] for record in identifiers)


def test_adversarial_corpus_rejects_artifact_drift(tmp_path):
    corpus = json.loads(validators.DEFAULT_CORPUS.read_text())
    corpus["artifacts"]["cases"]["sha256"] = "0" * 64
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(corpus))

    with pytest.raises(ValueError, match="cases"):
        validators.check_corpus(
            ROOT, path, validators.evaluate(validators.DEFAULT_CASES)
        )
