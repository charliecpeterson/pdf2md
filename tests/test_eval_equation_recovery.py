"""The equation-recovery gate pins crops, recognizer outputs, and regressions."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).parent.parent
SPEC = importlib.util.spec_from_file_location(
    "eval_equation_recovery", ROOT / "scripts" / "eval_equation_recovery.py"
)
equation_recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(equation_recovery)


def test_pinned_equation_recovery_matches_crops_and_implementation():
    report = equation_recovery.evaluate(
        ROOT / "tests" / "equation_recovery_corpus.json"
    )

    assert report["production"]["full_exact"] == 10
    assert report["production"]["components"]["subscript"] == {
        "correct": 29,
        "total": 30,
        "accuracy": 0.966667,
    }
    assert report["heldout_exact_control_regressions"] == 0
    assert report["production_geometry_verified"] == 6
    assert report["high_dpi_scan_diagnostic"]["full_exact"] is True
    assert report["high_dpi_scan_diagnostic"]["production_eligible"] is False


def test_diagnostic_scan_candidate_is_not_promoted():
    report = equation_recovery.evaluate(
        ROOT / "tests" / "equation_recovery_corpus.json"
    )

    assert report["targeted_component_exact"] == 3
    assert report["production"]["components"]["subscript"]["correct"] == 29
