"""The numeric-table differential check must distinguish errors from refusals."""

from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "eval_numeric_tables", Path(__file__).parent.parent / "scripts" / "eval_numeric_tables.py"
)
ent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ent)


def test_numeric_equivalence_ignores_decimal_formatting():
    assert ent._values_equal("6.150", "6.15")
    assert ent._values_equal("40", "4E+1")
    assert ent._values_equal("0.00", "0")
    assert not ent._values_equal("6.150", "6.151")


def _write_paddle_response(path, *contents):
    response = {
        "errorCode": 0,
        "result": {"layoutParsingResults": [{
            "prunedResult": {"parsing_res_list": [
                {"block_content": content} for content in contents
            ]}
        }]},
    }
    path.write_text(json.dumps(response))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_numeric_table_differential_outcomes(tmp_path):
    source_sha256 = "a" * 64
    version = tmp_path / source_sha256[:16] / "v1"
    candidate = version / "data" / "tables" / "table.json"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(json.dumps({"rows": [["1.00", "2.07"]]}))
    (version / "manifest.json").write_text(json.dumps({
        "representations": {"tables": [{
            "block_id": "#/table",
            "json": "data/tables/table.json",
        }]},
    }))
    (version / "provenance.json").write_text("{}")
    labels = {
        "documents": [{
            "source_sha256": source_sha256,
            "cells": [
                {"page": 1, "block_id": "#/table", "row": 0, "column": 0,
                 "expected": "1.00"},
                {"page": 1, "block_id": "#/table", "row": 0, "column": 1,
                 "expected": "2.01"},
                {"page": 1, "block_id": "#/table", "row": 1, "column": 0,
                 "expected": "3.00"},
            ],
        }],
    }

    report = ent.evaluate(tmp_path, labels)

    assert report["checked"] == 3
    assert report["agree"] == 1
    assert report["disagree"] == 1
    assert report["tool_refused"] == 1
    assert [record["outcome"] for record in report["records"]] == [
        "agree",
        "disagree",
        "tool_refused",
    ]


def test_paddle_cell_run_accepts_one_numeric_read_and_preserves_refusals(tmp_path):
    source_sha256 = "a" * 64
    response = tmp_path / "cell.paddle.json"
    response_sha256 = _write_paddle_response(response, "-4,404.0")
    crop_manifest = {
        "refusals": [{
            "source_sha256": source_sha256,
            "block_id": "#/table",
            "row": 0,
            "column": 1,
            "reason": "column_alignment_missing",
        }],
    }
    (tmp_path / "crops.json").write_text(json.dumps(crop_manifest))
    run = {
        "schema_version": 1,
        "source_manifest": "crops.json",
        "results": [{
            "source_sha256": source_sha256,
            "block_id": "#/table",
            "source_row": 0,
            "source_column": 0,
            "response": response.name,
            "response_sha256": response_sha256,
            "http_status": 200,
            "error_code": 0,
        }],
    }
    run_path = tmp_path / "paddle-run.json"
    run_path.write_text(json.dumps(run))
    labels = {"documents": [{
        "source_sha256": source_sha256,
        "cells": [
            {"block_id": "#/table", "row": 0, "column": 0},
            {"block_id": "#/table", "row": 0, "column": 1},
        ],
    }]}

    readings, refusals, _ = ent._paddle_cell_reference(labels, run_path)

    assert readings[source_sha256, "#/table", 0, 0] == "-4,404.0"
    assert refusals[source_sha256, "#/table", 0, 1] == "column_alignment_missing"


def test_paddle_cell_run_refuses_multiple_numeric_reads(tmp_path):
    response = tmp_path / "cell.paddle.json"
    response_sha256 = _write_paddle_response(response, "24.6 39.92")
    source_sha256 = "a" * 64
    run_path = tmp_path / "paddle-run.json"
    run_path.write_text(json.dumps({
        "schema_version": 1,
        "results": [{
            "source_sha256": source_sha256,
            "block_id": "#/table",
            "source_row": 0,
            "source_column": 0,
            "response": response.name,
            "response_sha256": response_sha256,
            "http_status": 200,
            "error_code": 0,
        }],
    }))
    labels = {"documents": [{
        "source_sha256": source_sha256,
        "cells": [{"block_id": "#/table", "row": 0, "column": 0}],
    }]}

    readings, refusals, _ = ent._paddle_cell_reference(labels, run_path)

    assert not readings
    assert refusals[source_sha256, "#/table", 0, 0] == "ambiguous_numeric_read"


def test_ocrflux_reference_uses_pinned_html_and_explicit_column_map(tmp_path):
    source_sha256 = "f" * 64
    markdown = tmp_path / "table.md"
    markdown.write_text(
        "<table><tr><td>1s</td><td>1.984</td><td>-0.913</td>"
        "<td>-0.958</td></tr></table>"
    )
    manifest_path = tmp_path / "ocrflux.json"
    manifest_path.write_text(json.dumps({
        "schema_version": 1,
        "results": [{
            "source_sha256": source_sha256,
            "block_id": "#/table",
            "markdown": markdown.name,
            "markdown_sha256": hashlib.sha256(markdown.read_bytes()).hexdigest(),
            "reference_shape": [1, 4],
            "column_map": {"1": 2, "2": 3},
        }],
    }))
    labels = {"documents": [{
        "source_sha256": source_sha256,
        "cells": [
            {"block_id": "#/table", "row": 0, "column": 1},
            {"block_id": "#/table", "row": 0, "column": 2},
        ],
    }]}

    readings, refusals, _ = ent._ocrflux_reference(labels, manifest_path)

    assert readings[source_sha256, "#/table", 0, 1] == "-0.913"
    assert readings[source_sha256, "#/table", 0, 2] == "-0.958"
    assert not refusals


def test_numeric_evaluation_can_pin_an_older_conversion_version(tmp_path):
    source_sha256 = "d" * 64
    for version, value in (("v1", "1.0"), ("v2", "9.0")):
        version_dir = tmp_path / source_sha256[:16] / version
        version_dir.mkdir(parents=True)
        (version_dir / "provenance.json").write_text(json.dumps({
            "tables": [{
                "block_id": "#/table",
                "gfm": f"| A |\n|---|\n| {value} |",
            }],
        }))
    labels = {"documents": [{
        "source_sha256": source_sha256,
        "version": "v1",
        "cells": [{
            "page": 1,
            "block_id": "#/table",
            "row": 1,
            "column": 0,
            "expected": "1.0",
        }],
    }]}

    report = ent.evaluate(tmp_path, labels)

    assert report["agree"] == 1


def test_cell_evidence_reference_uses_preserved_reader_values(tmp_path):
    source_sha256 = "e" * 64
    version = tmp_path / source_sha256[:16] / "v1"
    evidence = version / "data" / "tables" / "table.cells.jsonl"
    evidence.parent.mkdir(parents=True)
    (version / "provenance.json").write_text("{}")
    evidence.write_text("\n".join([
        json.dumps({
            "source_block_id": "#/table",
            "source_row": 0,
            "source_column": 0,
            "best_value": "1.0",
            "reader": "tesseract",
            "reader_value": "1.0",
        }),
        json.dumps({
            "source_block_id": "#/table",
            "source_row": 0,
            "source_column": 1,
            "best_value": "2.0",
            "reader": "tesseract",
            "reader_value": None,
            "reader_refusal_reason": "grid_alignment_failed",
        }),
    ]) + "\n")
    labels = {"documents": [{
        "source_sha256": source_sha256,
        "version": "v1",
        "cells": [
            {"block_id": "#/table", "row": 0, "column": 0},
            {"block_id": "#/table", "row": 0, "column": 1},
        ],
    }]}

    readings, refusals, info = ent._cell_evidence_reference(tmp_path, labels)

    assert readings[source_sha256, "#/table", 0, 0] == "1.0"
    assert refusals[source_sha256, "#/table", 0, 1] == "grid_alignment_failed"
    assert info["readers"] == ["tesseract"]
    assert info["evidence_files"] == 1
    assert len(info["evidence_sha256"]) == 64


def test_tesseract_tsv_maps_repeated_panels_and_sparse_rows():
    rows = [
        ["LEFT", "", "RIGHT", ""],
        ["R", "X", "R", "X"],
        ["0.1", "1.2", "0.1", "2.2"],
        ["0.2", ".", "0.2", "2.4"],
    ]
    _, layout = ent.split_repeated_panels(rows)
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    words = [
        # Three leading title/header lines exercise structural suffix alignment.
        # OCR quotes are literal TSV content; they must not consume following rows.
        "5\t1\t1\t1\t1\t1\t0\t0\t20\t10\t90\t\"LEFT",
        "5\t1\t1\t1\t2\t1\t0\t20\t20\t10\t90\tR",
        "5\t1\t1\t1\t3\t1\t5\t40\t10\t10\t90\tO.1",
        "5\t1\t1\t1\t3\t2\t45\t40\t10\t10\t90\t1.2",
        "5\t1\t1\t1\t3\t3\t105\t40\t10\t10\t90\t0.1",
        "5\t1\t1\t1\t3\t4\t145\t40\t10\t10\t90\t2.2",
        "5\t1\t1\t1\t4\t1\t5\t60\t10\t10\t90\t0.2",
        "5\t1\t1\t1\t4\t2\t45\t60\t10\t10\t90\t.",
        "5\t1\t1\t1\t4\t3\t105\t60\t10\t10\t90\t0.2",
        "5\t1\t1\t1\t4\t4\t145\t60\t10\t10\t90\t2.4",
    ]

    mapped = ent._map_tesseract_tsv(rows, "\n".join([header, *words]), layout)

    assert len(ent._word_lines("\n".join([header, *words]))) == 4
    assert mapped[2, 0] == "0.1"
    assert mapped[2, 3] == "2.2"
    assert mapped[3, 1] == "."


def test_reference_report_measures_disagreement_as_error_detector(tmp_path):
    source_sha256 = "b" * 64
    version = tmp_path / source_sha256[:16] / "v1"
    version.mkdir(parents=True)
    (version / "provenance.json").write_text(json.dumps({
        "tables": [{
            "block_id": "#/table",
            "gfm": "| A | B |\n|---|---|\n| 1.0 | 2.7 |",
        }],
    }))
    labels = {"documents": [{
        "source_sha256": source_sha256,
        "cells": [
            {"page": 1, "block_id": "#/table", "row": 1, "column": 0, "expected": "1.0"},
            {"page": 1, "block_id": "#/table", "row": 1, "column": 1, "expected": "2.0"},
        ],
    }]}
    reference = {
        (source_sha256, "#/table", 1, 0): "1.0",
        (source_sha256, "#/table", 1, 1): "2.0",
    }

    report = ent.evaluate(tmp_path, labels, reference)

    assert report["disagreement_detection"] == {
        "true_positive": 1,
        "false_positive": 0,
        "false_negative": 0,
        "true_negative": 1,
        "precision": 1.0,
        "recall": 1.0,
    }


def test_reference_refusal_does_not_compare_none_as_a_value(tmp_path):
    source_sha256 = "e" * 64
    version = tmp_path / source_sha256[:16] / "v1"
    version.mkdir(parents=True)
    (version / "provenance.json").write_text(json.dumps({
        "tables": [{
            "block_id": "#/table",
            "gfm": "| A |\n|---|\n| 1.0 |",
        }],
    }))
    labels = {"documents": [{
        "source_sha256": source_sha256,
        "cells": [{
            "page": 1,
            "block_id": "#/table",
            "row": 1,
            "column": 0,
            "expected": "1.0",
        }],
    }]}

    report = ent.evaluate(tmp_path, labels, {})

    assert report["reference"]["tool_refused"] == 1
    assert report["records"][0]["readers_agree"] is False


def test_numeric_table_report_scores_resolved_best_values(tmp_path):
    source_sha256 = "c" * 64
    version = tmp_path / source_sha256[:16] / "v1"
    table_dir = version / "data" / "tables"
    table_dir.mkdir(parents=True)
    (table_dir / "table.json").write_text(json.dumps({"rows": [["2.07"]]}))
    (table_dir / "table.cells.jsonl").write_text(json.dumps({
        "source_block_id": "#/table",
        "source_row": 0,
        "source_column": 0,
        "best_value": "2.01",
        "confidence": "medium",
        "resolution_basis": "local_continuity_reader",
    }) + "\n")
    (version / "manifest.json").write_text(json.dumps({
        "representations": {"tables": [{
            "block_id": "#/table",
            "json": "data/tables/table.json",
        }]},
    }))
    (version / "provenance.json").write_text("{}")
    labels = {"documents": [{
        "source_sha256": source_sha256,
        "cells": [{
            "page": 1,
            "block_id": "#/table",
            "row": 0,
            "column": 0,
            "expected": "2.01",
        }],
    }]}

    report = ent.evaluate(tmp_path, labels)

    assert report["resolved"] == {
        "checked": 1,
        "agree": 1,
        "disagree": 0,
        "changed": 1,
    }
    assert report["confidence_calibration"] == {
        "medium": {"checked": 1, "agree": 1, "disagree": 0},
    }
    assert report["records"][0]["resolution_basis"] == "local_continuity_reader"


def test_numeric_table_report_compares_canonical_grouped_numbers(tmp_path):
    source_sha256 = "d" * 64
    version = tmp_path / source_sha256[:16] / "v1"
    table_dir = version / "data" / "tables"
    table_dir.mkdir(parents=True)
    (table_dir / "table.json").write_text(json.dumps({
        "rows": [["-2 846.292", "-14.556 089"]],
    }))
    (table_dir / "table.cells.jsonl").write_text(json.dumps({
        "source_block_id": "#/table",
        "source_row": 0,
        "source_column": 1,
        "best_value": "-14.556089",
        "confidence": "high",
        "resolution_basis": "reader_agreement",
    }) + "\n")
    (version / "manifest.json").write_text(json.dumps({
        "representations": {"tables": [{
            "block_id": "#/table",
            "json": "data/tables/table.json",
        }]},
    }))
    (version / "provenance.json").write_text("{}")
    labels = {"documents": [{
        "source_sha256": source_sha256,
        "cells": [
            {"page": 1, "block_id": "#/table", "row": 0, "column": 0,
             "expected": "-2846.292"},
            {"page": 1, "block_id": "#/table", "row": 0, "column": 1,
             "expected": "-14.556089"},
        ],
    }]}

    report = ent.evaluate(tmp_path, labels)

    assert report["agree"] == 2
    assert report["records"][1]["best_outcome"] == "agree"
    assert report["resolved"]["changed"] == 0


def test_paddle_manifest_scores_only_structurally_aligned_cells(tmp_path):
    source_sha256 = "f" * 64
    version = tmp_path / source_sha256[:16] / "v1"
    table_dir = version / "data" / "tables"
    table_dir.mkdir(parents=True)
    rows = [
        ["LEFT", "", "RIGHT", ""],
        ["R", "X", "R", "X"],
        ["0.1", "1.0", "0.1", "2.0"],
        ["0.2", "1.1", "0.2", "2.1"],
    ]
    (table_dir / "table.json").write_text(json.dumps({"rows": rows}))
    (version / "manifest.json").write_text(json.dumps({
        "representations": {"tables": [{
            "block_id": "#/table",
            "json": "data/tables/table.json",
        }]},
    }))
    (version / "provenance.json").write_text("{}")
    labels = {"documents": [{
        "source_sha256": source_sha256,
        "cells": [
            {"page": 1, "block_id": "#/table", "row": 2, "column": 1,
             "expected": "1.0"},
            {"page": 1, "block_id": "#/table", "row": 2, "column": 3,
             "expected": "2.0"},
            {"page": 1, "block_id": "#/table", "row": 3, "column": 1,
             "expected": "1.1"},
        ],
    }]}
    response = {
        "errorCode": 0,
        "result": {"layoutParsingResults": [{"prunedResult": {
            "parsing_res_list": [{
                "block_label": "table",
                "block_content": (
                    "<table><tr><td>LEFT</td><td></td><td>RIGHT</td><td></td></tr>"
                    "<tr><td>R</td><td>X</td><td>R</td><td>X</td></tr>"
                    "<tr><td>0.1</td><td>1.0</td><td>0.1</td><td>2.7</td></tr>"
                    "</table>"
                ),
            }],
        }}]},
    }
    (tmp_path / "paddle.json").write_text(json.dumps(response))
    paddle_manifest = tmp_path / "paddle-manifest.json"
    paddle_manifest.write_text(json.dumps({
        "schema_version": 1,
        "tool": {"name": "PaddleOCR-VL"},
        "results": [{
            "source_sha256": source_sha256,
            "block_id": "#/table",
            "response": "paddle.json",
        }],
    }))

    reference, refusals, _ = ent._paddle_reference(
        tmp_path, labels, paddle_manifest
    )
    report = ent.evaluate(tmp_path, labels, reference, refusals)

    assert report["reference"] == {
        "agree": 1,
        "disagree": 1,
        "tool_refused": 1,
    }
    assert report["records"][2]["reference_refusal_reason"] == "paddle_cell_missing"
