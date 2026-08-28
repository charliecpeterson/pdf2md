"""Projection-crop evaluation keeps geometry and outcomes independently pinned."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parent.parent
SPEC = importlib.util.spec_from_file_location(
    "eval_projection_crops", ROOT / "scripts" / "eval_projection_crops.py"
)
projection_crops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(projection_crops)


def test_projection_box_uses_row_scaled_padding_and_clips_to_image():
    assert projection_crops.projection_cell_box(
        [20, 80], [10, 26], (100, 50)
    ) == [
        16, 8, 84, 28,
    ]
    assert projection_crops.projection_cell_box(
        [1, 99], [1, 49], (100, 50)
    ) == [
        0, 0, 100, 50,
    ]


def test_observation_separates_reader_refusal_from_low_confidence():
    record = {"crop_sha256": "a" * 64}
    result = {
        "input_sha256": "a" * 64,
        "text": "0.0012",
        "score": 0.98,
        "error": None,
    }

    assert projection_crops._observation(record, result) == ("0.0012", None)
    assert projection_crops._gated_observation(record, result) == (
        "0.0012", "reader_score_below_threshold",
    )
    result["input_sha256"] = "b" * 64
    assert projection_crops._observation(record, result) == (
        None, "input_hash_mismatch",
    )


def test_label_scoring_has_exact_three_outcomes():
    records = {
        key: {"crop_sha256": key * 64}
        for key in ("a", "b", "c")
    }
    results = {
        "a": {"input_sha256": "a" * 64, "text": "1.0", "score": 1.0},
        "b": {"input_sha256": "b" * 64, "text": "3.0", "score": 1.0},
        "c": {"input_sha256": "c" * 64, "text": "2.0", "score": 0.5},
    }
    labels = {
        "records": [
            {"id": "a", "expected": "1"},
            {"id": "b", "expected": "2"},
            {"id": "c", "expected": "2"},
        ]
    }

    assert projection_crops._score_labels(
        labels, records, results, gated=True
    ) == {"checked": 3, "agree": 1, "disagree": 1, "tool_refused": 1}


def test_fallback_keeps_the_reference_read_before_trying_projection():
    labels = {"records": [{"id": "a", "expected": "1"}]}
    old_records = {"a": {"crop_sha256": "a" * 64}}
    new_records = {"a": {"crop_sha256": "b" * 64}}
    old_results = {
        "a": {"input_sha256": "a" * 64, "text": "1", "score": 0.999}
    }
    new_results = {
        "a": {"input_sha256": "b" * 64, "text": "2", "score": 0.999}
    }

    assert projection_crops._score_fallback_labels(
        labels, old_records, old_results, new_records, new_results
    ) == {"checked": 1, "agree": 1, "disagree": 0, "tool_refused": 0}


def test_overlay_fallback_uses_the_projection_crop_hash():
    records = [{
        "id": "key",
        "role": "recovery",
        "source_block_id": "#/table",
        "panel": 0,
        "source_position": 1,
        "source_column": 0,
        "template_key": "0.1",
        "tesseract_value": "0.1",
        "crop_sha256": "a" * 64,
        "alignment_position": None,
    }]
    old_results = {
        "key": {"input_sha256": "a" * 64, "text": "0.1", "score": 0.5}
    }
    new_records = {"key": {**records[0], "crop_sha256": "b" * 64}}
    new_results = {
        "key": {"input_sha256": "b" * 64, "text": "0.1", "score": 0.999}
    }

    summary = projection_crops._overlay_summary(
        records, old_results, new_results, new_records
    )

    assert summary["confirmed_rows"] == {"recovery": 1}
    assert summary["cell_counts"] == {"recovery_accepted": 1}


def test_projection_crop_corpus_pins_the_differential_outcomes():
    corpus = json.loads((ROOT / "tests" / "projection_crop_corpus.json").read_text())

    assert corpus["expected"]["reader_parity"] == {
        "checked": 1088,
        "agree": 1016,
        "disagree": 72,
        "tool_refused": 0,
        "no_reference": 1,
    }
    assert corpus["expected"]["overlay"]["projection_fallback"][
        "cell_counts"
    ]["recovery_accepted"] == 541
