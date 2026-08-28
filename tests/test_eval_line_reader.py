"""Recognition-only evaluation keeps source identity and refusals explicit."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from PIL import Image


SCRIPTS = Path(__file__).parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location(
    "eval_line_reader", SCRIPTS / "eval_line_reader.py"
)
line_reader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(line_reader)


def test_prepare_crops_manual_source_and_preserves_provenance(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    source_crop = tmp_path / "table.png"
    Image.new("RGB", (100, 50), "white").save(source_crop)
    labels = {
        "schema_version": 1,
        "minimum_score": 0.99,
        "documents": [{
            "id": "scan",
            "source": source.name,
            "source_sha256": line_reader._sha256(source),
            "typeface": "scan",
            "role": "held_out",
            "page": 3,
            "source_crop": source_crop.name,
            "samples": [{
            "id": "row",
            "expected": "Cl 17",
            "primary": "Cl 17",
            "box": [0, 0, 50, 25],
            }],
        }],
    }

    prepared = line_reader.prepare(tmp_path, labels, tmp_path / "benchmark")

    record = prepared["records"][0]
    assert record["id"] == "scan:row"
    assert record["expected"] == "Cl 17"
    assert record["primary"] == "Cl 17"
    assert record["role"] == "held_out"
    assert prepared["documents"][0]["role"] == "held_out"
    assert record["source_crop_sha256"] == line_reader._sha256(source_crop)
    assert (tmp_path / "benchmark" / record["crop"]).is_file()
    inputs = json.loads((tmp_path / "benchmark" / "inputs.json").read_text())
    assert "expected" not in inputs["records"][0]


def test_evaluate_separates_wrong_reads_from_low_score_refusals():
    labels = {
        "minimum_score": 0.99,
        "documents": [{
            "id": "doc", "source": "doc.pdf", "source_sha256": "a" * 64,
            "typeface": "scan", "samples": 4,
        }],
        "records": [
            {"id": "doc:a", "expected": "HCl", "primary": "HCl", "crop_sha256": "1"},
            {"id": "doc:b", "expected": "ClO", "primary": "CIO", "crop_sha256": "2"},
            {"id": "doc:c", "expected": "ClF", "primary": "CIF", "crop_sha256": "3"},
            {"id": "doc:d", "expected": "Li₂", "primary": "Li2", "crop_sha256": "4"},
        ],
    }
    run = {"reader": {"model_name": "reader"}, "records": [
        {"id": "doc:a", "input_sha256": "1", "text": "hcl", "score": 0.999},
        {"id": "doc:b", "input_sha256": "2", "text": "CIO", "score": 0.999},
        {"id": "doc:c", "input_sha256": "3", "text": "CIF", "score": 0.98},
    ]}

    report = line_reader.evaluate(labels, run)

    assert report["checked"] == 4
    assert report["raw_reader"] == {"agree": 1, "disagree": 1, "tool_refused": 2}
    assert report["confirmation"] == {
        "agree": 1, "disagree": 1, "tool_refused": 2,
    }
    assert report["reader_refusal_reasons"] == {
        "score_below_threshold": 1,
        "result_missing": 1,
    }
    assert not report["gate_passed"]
    assert not report["heldout_gate_passed"]
    assert report["roles"]["development"]["checked"] == 4


def test_line_crop_uses_fixed_minimum_canvas_without_distortion():
    source = Image.new("RGB", (100, 50), "white")

    crop = line_reader._line_crop(source, [10, 10, 60, 30])

    assert crop.mode == "RGB"
    assert crop.size == (640, 192)


def test_key_normalization_preserves_decimal_points_and_signs():
    assert line_reader._key("Li₂") == line_reader._key("li2")
    assert line_reader._key("3p−") == line_reader._key("3p-")
    assert line_reader._key("3p-") != line_reader._key("3p")
    assert line_reader._key("0.0008") != line_reader._key("00008")
