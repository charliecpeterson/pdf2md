"""Exact internal checks support or flag extracted values without correcting them."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location(
    "eval_internal_scientific_checks",
    ROOT / "scripts" / "eval_internal_scientific_checks.py",
)
internal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(internal)


def test_exact_relations_use_decimal_arithmetic_and_refuse_text():
    assert internal._evaluate_relation("exact_equal", ["1.0", "1.00"]) == (
        "agree", "exact_identity"
    )
    assert internal._evaluate_relation("exact_sum", [".95", ".78", "1.73"]) == (
        "agree", "exact_identity"
    )
    assert internal._evaluate_relation("exact_product", ["8", "64", "512"]) == (
        "agree", "exact_identity"
    )
    assert internal._evaluate_relation("exact_equal", ["63690", "6369C"]) == (
        "tool_refused", "nonnumeric_operand"
    )


def test_pinned_internal_checks_find_natural_errors_without_replacements():
    report = internal.evaluate(internal.DEFAULT_CORPUS)

    assert internal.check_corpus(internal.DEFAULT_CORPUS, report)
    assert report["outcomes"] == {"agree": 21, "disagree": 2, "tool_refused": 1}
    assert report["contract"]["replacement_values_emitted"] is False
    assert {record["id"] for record in report["records"] if record["outcome"] == "disagree"} == {
        "nasa-repeated-ltipmax",
        "nasa-component-weight-sum",
    }
    assert all("replacement" not in record for record in report["records"])
