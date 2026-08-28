"""Source-row recovery keeps inferred rows separate and requires their own key read."""

from __future__ import annotations

import importlib.util
import json
from decimal import Decimal
from pathlib import Path


SCRIPTS = Path(__file__).parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location(
    "eval_source_row_recovery", SCRIPTS / "eval_source_row_recovery.py"
)
recovery = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(recovery)


def _table(block_id: str, keys: list[str]) -> dict:
    rows = ["| RADIUS | A |", "|---|---|"]
    rows.extend(f"| {key} | {index}.0 |" for index, key in enumerate(keys))
    return {"block_id": block_id, "gfm": "\n".join(rows)}


def test_canonical_sequence_prefers_the_longest_repeated_grid():
    tables = [
        _table("#/a", ["0.1", "0.2", "0.3"]),
        _table("#/b", ["0.1", "0.2", "0.3"]),
        _table("#/c", ["0.1", "0.2"]),
        _table("#/d", ["0.1", "0.2"]),
        _table("#/e", ["0.1", "0.2"]),
    ]

    assert recovery._canonical_sequence(tables) == ("0.1", "0.2", "0.3")


def test_recovery_starts_before_a_shifted_or_empty_structured_row():
    canonical = ("0.1", "0.2", "0.3", "0.4")
    shifted = {"rows": [["0.1", "1"], ["0.3", "2"], ["0.4", "3"]]}
    empty = {"rows": [["0.1", "1"], ["0.2", ""], ["0.3", "3"]]}

    assert recovery._recovery_start(shifted, canonical) == 0
    assert recovery._recovery_start(empty, canonical) == 1
    assert recovery._control_positions(20) == [0, 3, 5, 8, 11, 14, 16, 19]


def test_one_gap_alignment_requires_exact_anchors_and_most_keys():
    canonical = tuple(str(index) for index in range(10))
    observed = [Decimal(value) for value in canonical[:4] + canonical[5:]]
    observed[7] = Decimal("99")

    assert recovery._one_gap_position(observed, canonical) == (4, 8)

    observed[3] = Decimal("98")
    assert recovery._one_gap_position(observed, canonical) is None


def test_one_gap_alignment_refuses_an_unbracketed_edge_gap():
    canonical = tuple(str(index) for index in range(10))

    assert recovery._one_gap_position(
        [Decimal(value) for value in canonical[1:]], canonical
    ) is None


def test_intervening_panel_line_must_be_unique():
    def line(text: str, y: float) -> list[dict[str, object]]:
        return [{"text": text, "x": 20.0, "y": y}]

    middle = line("Q.Loo", 20.0)
    lines = [line("0.090", 10.0), middle, line("0.110", 30.0)]

    assert recovery._intervening_panel_line(lines, (10.0, 30.0), 10.0, 30.0) == (
        middle, middle
    )
    assert recovery._intervening_panel_line(
        lines + [line("noise", 21.0)], (10.0, 30.0), 10.0, 30.0
    ) is None


def test_apply_refuses_values_when_the_source_key_is_not_confirmed(tmp_path):
    version = tmp_path / "doc" / "v1"
    version.mkdir(parents=True)
    provenance = version / "provenance.json"
    provenance.write_text("{}")
    output = tmp_path / "recovery"
    output.mkdir()
    source_crop = output / "source.png"
    source_crop.write_bytes(b"source")
    crop = output / "cell.png"
    crop.write_bytes(b"cell")
    records = [
        {
            "id": "key", "role": "recovery", "page": 1,
            "source_block_id": "#/table", "panel": 0, "source_position": 2,
            "source_column": 0, "template_key": "0.3",
            "tesseract_value": "0.3", "raw_value": None,
            "source_crop": "source.png",
            "source_crop_sha256": recovery._sha256(source_crop),
            "source_box": [0, 0, 1, 1], "crop": "cell.png",
            "crop_sha256": recovery._sha256(crop),
        },
        {
            "id": "value", "role": "recovery", "page": 1,
            "source_block_id": "#/table", "panel": 0, "source_position": 2,
            "source_column": 1, "template_key": "0.3",
            "tesseract_value": "4.2", "raw_value": None,
            "source_crop": "source.png",
            "source_crop_sha256": recovery._sha256(source_crop),
            "source_box": [1, 0, 2, 1], "crop": "cell.png",
            "crop_sha256": recovery._sha256(crop),
        },
    ]
    manifest = {
        "schema_version": 1,
        "method": "source_panel_row_recovery_evaluation",
        "contract": {},
        "version_dir": str(version),
        "version_provenance_sha256": recovery._sha256(provenance),
        "source_sha256": "a" * 64,
        "panels": [], "refusals": [], "records": records,
    }
    (output / "manifest.json").write_text(json.dumps(manifest))
    run = output / "run.json"
    run.write_text(json.dumps({
        "reader": recovery.PINNED_READER,
        "records": [
            {
                "id": "key", "input_sha256": records[0]["crop_sha256"],
                "text": "0.8", "score": 0.999, "error": None,
            },
            {
                "id": "value", "input_sha256": records[1]["crop_sha256"],
                "text": "4.2", "score": 0.999, "error": None,
            },
        ],
    }))

    report = recovery.apply(output, run)
    row = json.loads((output / "rows.jsonl").read_text())

    assert report["recovery_rows_key_confirmed"] == 0
    assert report["cell_statuses"] == {"row_key_refused": 2}
    assert [cell["candidate_value"] for cell in row["cells"]] == [None, None]


def test_apply_refuses_shifted_panel_when_inferred_key_is_not_confirmed(tmp_path):
    version = tmp_path / "doc" / "v1"
    version.mkdir(parents=True)
    provenance = version / "provenance.json"
    provenance.write_text("{}")
    output = tmp_path / "recovery"
    output.mkdir()
    source_crop = output / "source.png"
    source_crop.write_bytes(b"source")
    crop = output / "cell.png"
    crop.write_bytes(b"cell")
    shared = {
        "page": 1, "source_block_id": "#/table", "panel": 0,
        "source_crop": "source.png",
        "source_crop_sha256": recovery._sha256(source_crop),
        "source_box": [0, 0, 1, 1], "crop": "cell.png",
        "crop_sha256": recovery._sha256(crop), "alignment_position": 2,
    }
    records = [
        {
            **shared, "id": "alignment", "role": "alignment",
            "source_position": 2, "source_column": 0, "template_key": "0.3",
            "tesseract_value": "Q.Loo", "raw_value": "0.3",
        },
        {
            **shared, "id": "key", "role": "recovery",
            "source_position": 3, "source_column": 0, "template_key": "0.4",
            "tesseract_value": "0.4", "raw_value": None,
        },
        {
            **shared, "id": "value", "role": "recovery",
            "source_position": 3, "source_column": 1, "template_key": "0.4",
            "tesseract_value": "4.2", "raw_value": None,
        },
    ]
    manifest = {
        "schema_version": 1, "method": "source_panel_row_recovery_evaluation",
        "contract": {}, "version_dir": str(version),
        "version_provenance_sha256": recovery._sha256(provenance),
        "source_sha256": "a" * 64, "panels": [], "refusals": [],
        "records": records,
    }
    (output / "manifest.json").write_text(json.dumps(manifest))
    run = output / "run.json"
    run.write_text(json.dumps({
        "reader": recovery.PINNED_READER,
        "records": [
            {
                "id": record["id"], "input_sha256": record["crop_sha256"],
                "text": text, "score": 0.999, "error": None,
            }
            for record, text in zip(records, ["9.9", "0.4", "4.2"])
        ],
    }))

    report = recovery.apply(output, run)
    rows = [json.loads(line) for line in (output / "rows.jsonl").read_text().splitlines()]

    assert report["alignment_rows_key_confirmed"] == 0
    assert report["alignment_checks"][0]["status"] == "alignment_key_refused"
    assert report["recovery_rows_key_confirmed"] == 0
    assert report["cell_statuses"] == {
        "alignment_key_refused": 1, "panel_alignment_refused": 2,
    }
    assert all(
        cell["candidate_value"] is None
        for row in rows
        for cell in row["cells"]
    )


def test_reader_inputs_expose_only_crop_identity():
    record = {
        "id": "sample", "crop": "crops/sample.png", "crop_sha256": "a" * 64,
        "template_key": "0.3", "tesseract_value": "4.2", "raw_value": "9.9",
    }
    inputs = {
        key: record[key] for key in ("id", "crop", "crop_sha256")
    }

    assert inputs == {
        "id": "sample", "crop": "crops/sample.png", "crop_sha256": "a" * 64,
    }


def test_apply_uses_projection_only_after_the_reference_gate_refuses(tmp_path):
    version = tmp_path / "doc" / "v1"
    version.mkdir(parents=True)
    provenance = version / "provenance.json"
    provenance.write_text("{}")
    output = tmp_path / "recovery"
    output.mkdir()
    source_crop = output / "source.png"
    source_crop.write_bytes(b"source")
    reference_crop = output / "reference.png"
    reference_crop.write_bytes(b"reference")
    projection_crop = output / "projection.png"
    projection_crop.write_bytes(b"projection")
    shared = {
        "role": "recovery",
        "page": 1,
        "source_block_id": "#/table",
        "panel": 0,
        "source_position": 2,
        "template_key": "0.3",
        "raw_value": None,
        "alignment_position": None,
        "source_crop": "source.png",
        "source_crop_sha256": recovery._sha256(source_crop),
        "source_box": [0, 0, 1, 1],
        "crop": "reference.png",
        "crop_sha256": recovery._sha256(reference_crop),
        "projection_crop": "projection.png",
        "projection_crop_sha256": recovery._sha256(projection_crop),
    }
    records = [
        {
            **shared,
            "id": "key",
            "source_column": 0,
            "tesseract_value": "0.3",
        },
        {
            **shared,
            "id": "reference-value",
            "source_column": 1,
            "tesseract_value": "4.2",
        },
        {
            **shared,
            "id": "fallback-value",
            "source_column": 2,
            "tesseract_value": "5.1",
        },
    ]
    manifest = {
        "schema_version": 1,
        "method": "source_panel_row_recovery_evaluation",
        "contract": {},
        "version_dir": str(version),
        "version_provenance_sha256": recovery._sha256(provenance),
        "source_sha256": "a" * 64,
        "panels": [],
        "refusals": [],
        "records": records,
    }
    (output / "manifest.json").write_text(json.dumps(manifest))

    reference_run = output / "run.json"
    reference_run.write_text(json.dumps({
        "reader": recovery.PINNED_READER,
        "records": [
            {
                "id": record["id"],
                "input_sha256": record["crop_sha256"],
                "text": text,
                "score": 0.999,
                "error": None,
            }
            for record, text in zip(records, ["0.8", "4.2", "9.9"])
        ],
    }))
    projection_run = output / "projection-run.json"
    projection_run.write_text(json.dumps({
        "reader": recovery.PINNED_READER,
        "records": [
            {
                "id": record["id"],
                "input_sha256": record["projection_crop_sha256"],
                "text": text,
                "score": 0.999,
                "error": None,
            }
            for record, text in zip(records, ["0.3", "8.8", "5.1"])
        ],
    }))

    report = recovery.apply(
        output, reference_run, projection_run_path=projection_run
    )
    row = json.loads((output / "rows.jsonl").read_text())

    assert report["recovery_rows_key_confirmed"] == 1
    assert report["accepted_readers"] == {"projection": 2, "reference": 1}
    assert [cell["candidate_value"] for cell in row["cells"]] == [
        "0.3", "4.2", "5.1",
    ]
    assert [cell["status"] for cell in row["cells"]] == [
        "template_projection_fallback_candidate",
        "two_reader_candidate",
        "projection_fallback_candidate",
    ]


def test_source_labels_are_hash_pinned_and_keep_refusals_explicit():
    manifest = {"source_sha256": "a" * 64}
    rows = [{"cells": [
        {"id": "right", "candidate_value": "1.20", "crop_sha256": "1"},
        {"id": "wrong", "candidate_value": "2.0", "crop_sha256": "2"},
        {"id": "refused", "candidate_value": None, "crop_sha256": "3"},
    ]}]
    labels = {
        "schema_version": 1,
        "source_sha256": "a" * 64,
        "records": [
            {"id": "right", "expected": "1.2", "crop_sha256": "1"},
            {"id": "wrong", "expected": "2.1", "crop_sha256": "2"},
            {"id": "refused", "expected": "3.0", "crop_sha256": "3"},
        ],
    }

    assert recovery._score_labels(manifest, rows, labels) == {
        "checked": 3, "agree": 1, "disagree": 1, "tool_refused": 1,
    }
