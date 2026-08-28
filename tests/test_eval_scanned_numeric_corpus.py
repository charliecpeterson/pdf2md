"""The scanned corpus keeps known errors separate from clean controls."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "eval_scanned_numeric_corpus",
    SCRIPTS / "eval_scanned_numeric_corpus.py",
)
corpus = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(corpus)
finally:
    sys.path.pop(0)


def _write_case(root: Path, name: str, source_hash: str, actual: str) -> None:
    version = root / "outputs" / name / source_hash[:16] / "v1"
    table_path = version / "data" / "tables" / "table.json"
    table_path.parent.mkdir(parents=True)
    table_path.write_text(json.dumps({"rows": [[actual]]}))
    (version / "manifest.json").write_text(json.dumps({
        "representations": {"tables": [{
            "block_id": "#/table",
            "json": "data/tables/table.json",
        }]},
    }))
    (version / "provenance.json").write_text("{}")
    labels = {
        "documents": [{
            "source": f"{name}.pdf",
            "source_sha256": source_hash,
            "version": "v1",
            "cells": [{
                "page": 1,
                "block_id": "#/table",
                "row": 0,
                "column": 0,
                "expected": "1.0",
            }],
        }],
    }
    labels_path = root / "labels" / f"{name}.json"
    labels_path.parent.mkdir(exist_ok=True)
    labels_path.write_text(json.dumps(labels))


def test_corpus_aggregates_errors_and_controls_by_role(tmp_path):
    _write_case(tmp_path, "error", "a" * 64, "7.0")
    _write_case(tmp_path, "control", "b" * 64, "1.0")
    manifest = {
        "schema_version": 1,
        "cases": [
            {
                "id": "error",
                "role": "known_primary_errors",
                "labels": "labels/error.json",
                "out_dir": "outputs/error",
                "expected_primary": {
                    "checked": 1, "agree": 0, "disagree": 1, "tool_refused": 0,
                },
            },
            {
                "id": "control",
                "role": "source_checked_controls",
                "labels": "labels/control.json",
                "out_dir": "outputs/control",
                "expected_primary": {
                    "checked": 1, "agree": 1, "disagree": 0, "tool_refused": 0,
                },
            },
        ],
    }

    report = corpus.evaluate(tmp_path, manifest)

    assert (report["checked"], report["agree"], report["disagree"]) == (2, 1, 1)
    assert report["roles"]["known_primary_errors"]["disagree"] == 1
    assert report["roles"]["source_checked_controls"]["agree"] == 1
    assert report["baseline_matches"]


def test_corpus_detects_baseline_drift(tmp_path):
    _write_case(tmp_path, "control", "c" * 64, "1.0")
    manifest = {
        "schema_version": 1,
        "cases": [{
            "id": "control",
            "role": "source_checked_controls",
            "labels": "labels/control.json",
            "out_dir": "outputs/control",
            "expected_primary": {
                "checked": 1, "agree": 0, "disagree": 1, "tool_refused": 0,
            },
        }],
    }

    report = corpus.evaluate(tmp_path, manifest)

    assert not report["baseline_matches"]
    assert not report["cases"][0]["baseline_matches"]


def test_pinned_scanned_corpus_covers_four_documents_with_natural_errors():
    manifest = json.loads((ROOT / "tests" / "scanned_numeric_corpus.json").read_text())

    report = corpus.evaluate(ROOT, manifest)

    assert {
        key: report[key]
        for key in ("checked", "agree", "disagree", "tool_refused")
    } == {"checked": 124, "agree": 104, "disagree": 20, "tool_refused": 0}
    assert report["roles"]["known_primary_errors"] == {
        "checked": 20,
        "agree": 0,
        "disagree": 20,
        "tool_refused": 0,
    }
    assert report["roles"]["source_checked_controls"] == {
        "checked": 104,
        "agree": 104,
        "disagree": 0,
        "tool_refused": 0,
    }
    assert report["baseline_matches"]
