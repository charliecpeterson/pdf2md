"""Candidate mining keeps disagreement evidence separate from source labels."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "mine_numeric_reader_disagreements",
    SCRIPTS / "mine_numeric_reader_disagreements.py",
)
miner = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(miner)
finally:
    sys.path.pop(0)


def test_report_separates_agreement_disagreement_and_refusal(tmp_path, monkeypatch):
    version_dir = tmp_path / "document" / "v3"
    table_dir = version_dir / "data" / "tables"
    crop_dir = version_dir / "assets"
    table_dir.mkdir(parents=True)
    crop_dir.mkdir()
    (version_dir / "provenance.json").write_text(json.dumps({
        "source_path": "scan.pdf",
        "source_sha256": "a" * 64,
    }))
    (version_dir / "manifest.json").write_text(json.dumps({
        "representations": {"tables": [{
            "block_id": "#/tables/0",
            "page": 7,
            "json": "data/tables/table.json",
            "crop": "assets/table.png",
        }]},
    }))
    (table_dir / "table.json").write_text(json.dumps({
        "rows": [["row", "1.25", "2.50", "3.75"]],
    }))
    (crop_dir / "table.png").write_bytes(b"crop")
    monkeypatch.setattr(
        miner,
        "_tesseract_reference",
        lambda *_: ({
            ("#/tables/0", 0, 1): "1.250",
            ("#/tables/0", 0, 2): "2.80",
        }, []),
    )
    monkeypatch.setattr(
        miner.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="tesseract 5.5.0\n"),
    )

    report = miner.evaluate(version_dir, "tesseract")

    assert (report["checked"], report["agree"], report["disagree"]) == (3, 1, 1)
    assert report["tool_refused"] == 1
    assert [record["outcome"] for record in report["records"]] == [
        "agree", "disagree", "tool_refused"
    ]
    assert report["records"][2]["refusal_reason"] == "cell_alignment_missing"
    assert report["records"][0]["source_crop"] == "assets/table.png"


def test_report_identifies_missing_source_crop(tmp_path, monkeypatch):
    version_dir = tmp_path / "document" / "v1"
    table_dir = version_dir / "data" / "tables"
    table_dir.mkdir(parents=True)
    (version_dir / "provenance.json").write_text(json.dumps({
        "source_path": "scan.pdf",
        "source_sha256": "b" * 64,
    }))
    (version_dir / "manifest.json").write_text(json.dumps({
        "representations": {"tables": [{
            "block_id": "#/tables/0",
            "page": 2,
            "json": "data/tables/table.json",
        }]},
    }))
    (table_dir / "table.json").write_text(json.dumps({"rows": [["4.2"]]}))
    monkeypatch.setattr(
        miner,
        "_tesseract_reference",
        lambda *_: ({}, ["#/tables/0"]),
    )
    monkeypatch.setattr(
        miner.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="tesseract 5.5.0\n"),
    )

    report = miner.evaluate(version_dir, "tesseract")

    assert report["records"][0]["refusal_reason"] == "source_crop_missing"
