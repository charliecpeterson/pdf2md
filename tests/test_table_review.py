"""Review-sheet sampling, source links, and evaluator-compatible label export."""

from __future__ import annotations

import csv
import json

from PIL import Image

from pdf2md.table_review import create_table_review


def _version(tmp_path):
    version = tmp_path / "out" / "abc" / "v1"
    table_dir = version / "data" / "tables"
    table_dir.mkdir(parents=True)
    assets = version / "assets"
    assets.mkdir()
    Image.new("RGB", (300, 120), "white").save(assets / "table.png")
    (version / "provenance.json").write_text(json.dumps({
        "source_sha256": "a" * 64,
        "source_path": "/papers/source.pdf",
    }))
    (version.parent / "source.pdf").write_bytes(b"pdf")

    for table_number in (1, 2):
        block_id = f"#/tables/{table_number}"
        rows = [["r", "a", "b"], ["0.1", "1.0", "2.0"]]
        (table_dir / f"table_{table_number}.json").write_text(json.dumps({
            "block_id": block_id,
            "page": table_number,
            "source_crop": "assets/table.png",
            "rows": rows,
        }))
        evidence = []
        for column, confidence in enumerate(("low", "medium", "high")):
            evidence.append({
                "page": table_number,
                "source_block_id": block_id,
                "source_row": 1,
                "source_column": column,
                "value_status": "numeric",
                "primary_value": rows[1][column],
                "reader_value": rows[1][column],
                "best_value": rows[1][column],
                "confidence": confidence,
                "resolution_basis": "independent_reader_agreement",
                "verification_status": "reader_agreement",
                "semantic_key": {
                    "atomic_number": "2", "symbol": "HE", "row_key": "0.1",
                    "column": str(column),
                },
            })
        (table_dir / f"table_{table_number}.cells.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in evidence)
        )
    return version


def test_review_sheet_is_stratified_deterministic_and_prefills_labels(tmp_path):
    version = _version(tmp_path)
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({
        "schema_version": 1,
        "documents": [{
            "source_sha256": "a" * 64,
            "source": "source.pdf",
            "cells": [{
                "page": 1, "block_id": "#/tables/1", "row": 1, "column": 0,
                "expected": "0.1", "label": "known <cell>",
            }],
        }],
    }))

    first = create_table_review(
        version, output_path=tmp_path / "first.html", sample_size=4, seed=7,
        per_table=1, labels_path=labels,
    )
    second = create_table_review(
        version, output_path=tmp_path / "second.html", sample_size=4, seed=7,
        per_table=1, labels_path=labels,
    )

    assert first["sampled"] == 4
    assert first["available"] == 6
    assert first["prefilled"] == 1
    assert set(first["sample_counts"]) == {"low", "medium", "high"}
    with first["csv"].open(newline="") as stream:
        first_rows = list(csv.DictReader(stream))
    with second["csv"].open(newline="") as stream:
        second_rows = list(csv.DictReader(stream))
    assert first_rows == second_rows
    known = next(row for row in first_rows if row["expected"])
    assert known["expected"] == "0.1"
    assert known["label"] == "known <cell>"

    page = first["html"].read_text()
    assert "known &lt;cell&gt;" in page
    assert "Download completed labels JSON" in page
    assert "source.pdf#page=1" in page
    assert "assets/table.png" in page


def test_review_sheet_requires_numeric_evidence(tmp_path):
    version = tmp_path / "v1"
    version.mkdir()
    (version / "provenance.json").write_text("{}")

    try:
        create_table_review(version)
    except ValueError as error:
        assert "no numeric cell evidence" in str(error)
    else:
        raise AssertionError("missing evidence should refuse review generation")


def test_review_sheet_renders_missing_source_crop_from_provenance(tmp_path, monkeypatch):
    version = _version(tmp_path)
    provenance_path = version / "provenance.json"
    provenance = json.loads(provenance_path.read_text())
    provenance["blocks"] = [{
        "id": f"#/tables/{table_number}",
        "type": "table",
        "page": table_number,
        "bbox": {"x0": 10, "y0": 80, "x1": 90, "y1": 20},
    } for table_number in (1, 2)]
    provenance_path.write_text(json.dumps(provenance))
    for table_number in (1, 2):
        table_path = version / "data" / "tables" / f"table_{table_number}.json"
        table = json.loads(table_path.read_text())
        table["source_crop"] = None
        table_path.write_text(json.dumps(table))

    class FakeRenderer:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def crop(self, page, bbox, path, *, dpi):
            Image.new("RGB", (320, 140), "white").save(path)

    monkeypatch.setattr("pdf2md.table_review.CropRenderer", FakeRenderer)

    report = create_table_review(version, sample_size=1, per_table=1)

    page = report["html"].read_text()
    assert "No source crop is available" not in page
    assert "assets/table-review/tables_" in page
