"""Natural-error evaluation keeps value, reader, and structure outcomes separate."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "eval_natural_numeric_error_corpus",
    SCRIPTS / "eval_natural_numeric_error_corpus.py",
)
natural = importlib.util.module_from_spec(SPEC)
try:
    SPEC.loader.exec_module(natural)
finally:
    sys.path.pop(0)


def test_natural_numeric_corpus_matches_pinned_multidocument_result():
    corpus = json.loads((ROOT / "tests" / "natural_numeric_error_corpus.json").read_text())

    report = natural.evaluate(ROOT, corpus)

    assert report["documents_examined"] == 33
    assert report["documents_with_reviewed_cells"] == 31
    assert report["primary"] == {
        "checked": 2113,
        "agree": 2099,
        "disagree": 14,
        "tool_refused": 0,
    }
    assert report["reader"] == {
        "checked": 2113,
        "agree": 1078,
        "disagree": 182,
        "tool_refused": 853,
    }
    assert report["structure"] == corpus["expected"]["structure"]
    assert len(report["documents"]) == 33
    assert len(report["records"]) == 2113
    assert len({record["id"] for record in report["records"]}) == 2113
    assert sum(
        record["primary_outcome"] == "disagree" for record in report["records"]
    ) == 14
    assert sum(
        record["reader_outcome"] == "tool_refused" for record in report["records"]
    ) == 853
    assert sum(document["primary"]["checked"] > 0 for document in report["documents"]) == 31
    assert all("rates" in document for document in report["documents"])

    cases = {case["id"]: case for case in report["cases"]}
    assert cases["ornl_iterative_output"]["primary"]["disagree"] == 8
    assert cases["nasa_wates_output"]["primary"]["disagree"] == 5
    assert cases["nist_nitrogen"]["primary"]["agree"] == 12
    assert cases["nist_nitrogen"]["structure"]["auxiliary_geometry_refusals"] == 431


def test_pooled_result_is_explicitly_not_a_prevalence_estimate():
    corpus = json.loads((ROOT / "tests" / "natural_numeric_error_corpus.json").read_text())

    report = natural.evaluate(ROOT, corpus)

    assert report["contract"]["sampling"].endswith("not a prevalence estimate")
    assert {case["role"] for case in report["cases"]} == {
        "independent_reference_accepted_rows",
        "whole_extracted_table_review",
        "targeted_syntax_and_geometry_coverage",
        "error_enriched_source_pixel_review",
        "targeted_geometry_failure_review",
    }
