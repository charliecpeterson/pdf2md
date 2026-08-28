"""Rendering variants preserve geometry and keep instability scoring off baseline."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).parent.parent
SPEC = importlib.util.spec_from_file_location(
    "eval_rendering_stability", ROOT / "scripts" / "eval_rendering_stability.py"
)
stability = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stability)


def test_scaled_box_tracks_both_render_dimensions():
    assert stability._scaled_box([10, 20, 30, 40], (100, 100), (200, 150)) == (
        20, 30, 60, 60
    )


def test_adaptive_binary_emits_only_black_and_white():
    image = Image.new("L", (25, 15), 220)
    for x in range(8, 17):
        for y in range(4, 11):
            image.putpixel((x, y), 20)

    binary = stability._adaptive_binary(image, 300)

    assert set(np.unique(np.asarray(binary))) == {0, 255}
    assert binary.getpixel((12, 7)) == 0
    assert binary.getpixel((0, 0)) == 255


def test_variant_identity_pins_all_four_factors():
    variants = {
        stability._variant_id(dpi, pixels, deskew, padding)
        for dpi in stability.DPIS
        for pixels in stability.PIXEL_MODES
        for deskew in stability.DESKEW_MODES
        for padding in stability.PADDING_MODES
    }

    assert len(variants) == 24
    assert stability.BASELINE_VARIANT in variants


def test_instability_predicts_baseline_adverse_without_using_baseline():
    baseline = stability.BASELINE_VARIANT
    records = [
        {
            "id": "unstable|baseline",
            "base_id": "unstable",
            "variant": baseline,
            "expected": "1.0",
            "expected_kind": "numeric",
            "primary": "1.0",
        },
        {
            "id": "unstable|first",
            "base_id": "unstable",
            "variant": "first",
            "expected": "1.0",
            "expected_kind": "numeric",
            "primary": "1.0",
        },
        {
            "id": "unstable|second",
            "base_id": "unstable",
            "variant": "second",
            "expected": "1.0",
            "expected_kind": "numeric",
            "primary": "1.0",
        },
        {
            "id": "stable|baseline",
            "base_id": "stable",
            "variant": baseline,
            "expected": "2.0",
            "expected_kind": "numeric",
            "primary": "2.0",
        },
        {
            "id": "stable|first",
            "base_id": "stable",
            "variant": "first",
            "expected": "2.0",
            "expected_kind": "numeric",
            "primary": "2.0",
        },
        {
            "id": "stable|second",
            "base_id": "stable",
            "variant": "second",
            "expected": "2.0",
            "expected_kind": "numeric",
            "primary": "2.0",
        },
    ]
    results = {
        "unstable|baseline": {"text": "1.0", "score": 0.5},
        "unstable|first": {"text": "1.0", "score": 0.999},
        "unstable|second": {"text": "1.1", "score": 0.999},
        "stable|baseline": {"text": "2.0", "score": 0.999},
        "stable|first": {"text": "2.0", "score": 0.999},
        "stable|second": {"text": "2.0", "score": 0.999},
    }

    summary = stability._prediction_summary(records, results)

    assert summary["unstable_cells"] == 1
    assert summary["unstable_baseline_adverse"] == 1
    assert summary["stable_cells"] == 1
    assert summary["stable_baseline_adverse"] == 0
    assert summary["baseline_adverse_sensitivity"] == 1.0
    assert summary["baseline_adverse_specificity"] == 1.0
    assert summary["baseline_adverse_positive_predictive_value"] == 1.0
    assert summary["baseline_adverse_negative_predictive_value"] == 1.0


def test_pinned_rendering_corpus_matches_reader_run():
    output_dir = ROOT / "out" / "reviews" / "rendering-stability-v1"
    report = stability.compare(output_dir, output_dir / "run.json")

    assert stability.check_corpus(
        ROOT, ROOT / "tests" / "rendering_stability_corpus.json", report
    )
