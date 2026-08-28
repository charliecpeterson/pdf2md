"""Projection stress cases preserve references and fail closed on bad bands."""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).parent.parent
SPEC = importlib.util.spec_from_file_location(
    "eval_projection_row_stress",
    ROOT / "scripts" / "eval_projection_row_stress.py",
)
stress = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stress)


def test_case_outcomes_keep_raw_error_separate_from_gate_refusal():
    centers = [(10.0, 15.0), (10.0, 35.0)]

    assert stress._case_outcomes(centers, [(10, 20), (30, 40)]) == (
        "agree", "agree", [],
    )
    assert stress._case_outcomes(centers, [(10, 20), (40, 50)]) == (
        "disagree", "tool_refused", [1],
    )
    assert stress._case_outcomes(centers, None) == (
        "tool_refused", "tool_refused", [0, 1],
    )


def test_known_rotation_and_translation_update_reference_centers():
    rotated = stress._rotate_point((60.0, 50.0), (100, 100), 90.0)

    assert math.isclose(rotated[0], 50.0, abs_tol=1e-9)
    assert math.isclose(rotated[1], 40.0, abs_tol=1e-9)

    source = Image.new("RGB", (100, 80), "white")
    ImageDraw.Draw(source).rectangle((20, 30, 30, 40), fill="black")
    transformed, centers, operations = stress._apply_operations(
        source,
        [(25.0, 35.0)],
        [{"type": "translate", "dx": 10, "dy": 5}, {"type": "crop", "top": 3}],
        0,
        1,
    )

    assert transformed.size == (100, 77)
    assert centers == [(35.0, 37.0)]
    assert operations == [
        {"type": "translate", "dx": 10, "dy": 5},
        {"type": "crop", "top": 3},
    ]
    transformed.close()
    source.close()


def test_stress_corpus_has_unique_cases_and_pinned_outcomes():
    corpus = json.loads((ROOT / "tests" / "projection_row_stress_corpus.json").read_text())
    cases = corpus["cases"]
    outcomes = {"agree", "disagree", "tool_refused"}

    assert len(cases) == 28
    assert len({case["id"] for case in cases}) == len(cases)
    assert {case["expected_raw_outcome"] for case in cases} <= outcomes
    assert {case["expected_gate_outcome"] for case in cases} <= outcomes
    assert corpus["expected"]["accepted_wrong_mappings"] == 0
    assert corpus["expected"]["detected_raw"] == {
        "agree": 23,
        "disagree": 4,
        "tool_refused": 1,
    }
    assert corpus["expected"]["detected_accepted_wrong_mappings"] == 0
    assert corpus["expected"]["column_rows_exact"] == 1602
    assert corpus["expected"]["column_rows_refused"] == 190
