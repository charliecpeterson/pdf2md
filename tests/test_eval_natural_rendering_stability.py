"""Natural-error instability is scored beside the existing clean-control frame."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location(
    "eval_natural_rendering_stability",
    ROOT / "scripts" / "eval_natural_rendering_stability.py",
)
natural = importlib.util.module_from_spec(spec)
spec.loader.exec_module(natural)


def test_padding_expands_human_box_without_crossing_image():
    assert natural._padded([3, 2, 98, 49], (100, 50)) == [0, 0, 100, 50]


def test_combined_prediction_scores_primary_error_detection():
    base = {
        "instability_prediction": {
            "cells": [{
                "base_id": "clean",
                "unstable_off_baseline": False,
                "primary_outcome": "agree",
            }]
        }
    }
    error = {
        "instability_prediction": {
            "cells": [{
                "base_id": "error",
                "unstable_off_baseline": True,
                "primary_outcome": "disagree",
            }]
        },
    }

    prediction = natural._combined_prediction(
        base, error, [{"base_id": "error", "document": "scan"}]
    )

    assert prediction["primary_error_sensitivity"] == 1.0
    assert prediction["primary_error_specificity"] == 1.0
    assert prediction["primary_error_positive_predictive_value"] == 1.0
    assert prediction["primary_error_negative_predictive_value"] == 1.0


def test_pinned_natural_rendering_stability_result():
    output = ROOT / "out" / "reviews" / "rendering-stability-natural-errors-v1"
    report = natural.compare(
        output,
        output / "run.json",
        ROOT / "out" / "reviews" / "rendering-stability-v1" / "report.json",
    )

    assert natural.check_corpus(
        ROOT, ROOT / "tests" / "natural_rendering_stability_corpus.json", report
    )
    assert report["combined_instability_prediction"]["unstable_primary_errors"] == 13
    assert report["combined_instability_prediction"]["unstable_clean_primary"] == 15
