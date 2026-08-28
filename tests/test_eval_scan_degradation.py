"""Controlled scan evaluation distinguishes wrong cells from structural refusal."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "eval_scan_degradation", SCRIPTS / "eval_scan_degradation.py"
)
scan = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(scan)
finally:
    sys.path.pop(0)


def test_scan_degradation_reports_agreement_error_and_refusal(tmp_path):
    corpus_pdf = tmp_path / "corpus.pdf"
    corpus_pdf.write_bytes(b"pdf")
    manifest = {
        "schema_version": 1,
        "manifest_path": str(tmp_path / "corpus.manifest.json"),
        "source_sha256": "a" * 64,
        "corpus_pdf": corpus_pdf.name,
        "corpus_sha256": scan._sha256(corpus_pdf),
        "variants": [
            {"id": "clean", "page": 0, "operations": []},
            {"id": "hard", "page": 1, "operations": ["blur"]},
        ],
    }
    ground_truth = {
        "schema_version": 1,
        "source": "source.pdf",
        "source_sha256": "a" * 64,
        "columns": ["left", "right"],
        "rows": [
            {"key": "Li2", "values": ["1.0", "2.0"]},
            {"key": "NaCl", "values": ["3.0", "4.0"]},
        ],
    }
    version_dir = tmp_path / "v1"
    table_dir = version_dir / "data" / "tables"
    table_dir.mkdir(parents=True)
    tables = [
        ("clean", 0, [["Li₂", "1.00", "2"], ["NaCl", "3", "4"]]),
        ("hard", 1, [["Li_2", "1.00", "9"], ["missing", "3", "4"]]),
    ]
    representations = []
    for name, page, rows in tables:
        table_path = table_dir / f"{name}.json"
        table_path.write_text(json.dumps({"rows": rows}))
        representations.append({
            "block_id": f"#/tables/{name}",
            "page": page,
            "json": table_path.relative_to(version_dir).as_posix(),
        })
    (version_dir / "manifest.json").write_text(json.dumps({
        "representations": {"tables": representations},
    }))
    (version_dir / "provenance.json").write_text("{}")
    evidence = table_dir / "values.cells.jsonl"
    evidence.write_text("\n".join(
        json.dumps({
            "source_block_id": block_id,
            "source_row": row,
            "source_column": column,
            "reader_value": reader,
            "best_value": best,
            "confidence": "high",
            "resolution_basis": "reader_agreement",
        })
        for block_id, row, column, reader, best in [
            ("#/tables/clean", 0, 1, "1.0", "1.0"),
            ("#/tables/clean", 0, 2, "2.0", "2.0"),
            ("#/tables/clean", 1, 1, "3.0", "3.0"),
            ("#/tables/clean", 1, 2, "4.0", "4.0"),
            ("#/tables/hard", 0, 1, "1.0", "1.0"),
            ("#/tables/hard", 0, 2, "2.0", "2.0"),
        ]
    ) + "\n")

    report = scan.evaluate(version_dir, ground_truth, manifest)

    assert (report["checked"], report["agree"]) == (8, 5)
    assert report["disagree"] == 1
    assert report["tool_refused"] == 2
    assert report["reader"] == {"agree": 6, "disagree": 0, "tool_refused": 2}
    assert report["best"] == {"agree": 6, "disagree": 0, "tool_refused": 2}
    assert report["variants"][0]["agree"] == 4
    assert report["variants"][1]["tool_refused"] == 2


def test_row_key_normalizes_subscripts_and_latex():
    assert scan._row_key("$N_2$") == "n2"
    assert scan._row_key("N₂") == "n2"


def test_ablation_summary_attributes_recovered_cells_to_removed_factor():
    variant_reports = [
        {
            "id": "clean",
            "role": "control",
            "agree": 2,
            "disagree": 0,
            "tool_refused": 0,
        },
        {
            "id": "combined",
            "role": "full_combination",
            "agree": 1,
            "disagree": 0,
            "tool_refused": 1,
        },
        {
            "id": "without_blur",
            "role": "leave_one_out",
            "removed_factor": "blur",
            "agree": 2,
            "disagree": 0,
            "tool_refused": 0,
        },
    ]
    records = [
        {"variant": "combined", "row_key": "ClF", "column": "E", "outcome": "tool_refused"},
        {"variant": "combined", "row_key": "SO", "column": "E", "outcome": "agree"},
        {"variant": "without_blur", "row_key": "ClF", "column": "E", "outcome": "agree"},
        {"variant": "without_blur", "row_key": "SO", "column": "E", "outcome": "agree"},
    ]

    summary = scan._ablation_summary(variant_reports, records)

    assert summary["full"] == {"agree": 1, "disagree": 0, "tool_refused": 1}
    assert summary["leave_one_out"]["blur"] == {
        "variant": "without_blur",
        "agree_delta": 1,
        "disagree_delta": 0,
        "tool_refused_delta": -1,
        "recovered_cells": [{"row_key": "ClF", "column": "E"}],
        "recovered_rows": ["ClF"],
        "introduced_failures": [],
    }
