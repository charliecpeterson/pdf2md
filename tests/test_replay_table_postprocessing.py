"""Postprocessing replay creates a distinct, source-pinned document version."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import asdict
from pathlib import Path

from pdf2md.schema import Block, BlockType, TableData


ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location(
    "replay_table_postprocessing",
    ROOT / "scripts" / "replay_table_postprocessing.py",
)
replay_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay_module)


def test_replay_rebuilds_normalization_and_pins_source_version(tmp_path):
    source = tmp_path / "document" / "v1"
    target = source.parent / "v2"
    table_dir = source / "data" / "tables"
    table_dir.mkdir(parents=True)
    block = Block("#/table", BlockType.TABLE, "", 1, extra={"ocr": True})
    table = TableData(
        block.id,
        1,
        None,
        "\n".join([
            "| ATOM 1 | | ATOM 2 | |",
            "|---|---|---|---|",
            "| RADIUS | 1S | RADIUS | 1S |",
            "| 0.1 | 1.0 | 0.1 | 2.0 |",
        ]),
        json_path="data/tables/table.json",
    )
    (table_dir / "table.json").write_text(json.dumps({
        "block_id": block.id,
        "normalized_csv": None,
        "normalized_json": None,
    }))
    (source / "profile.json").write_text(json.dumps({"derived_table_datasets": 1}))
    (source / "manifest.json").write_text(json.dumps({
        "document": {"version": 1},
        "representations": {"tables": [{
            "block_id": block.id,
            "normalized_csv": None,
            "normalized_json": None,
        }]},
    }))
    (source / "provenance.json").write_text(json.dumps({
        "version": 1,
        "blocks": [asdict(block)],
        "tables": [asdict(table)],
        "provenance": {
            "started_at": "old",
            "finished_at": "old",
            "duration_s": 1,
            "run_fingerprint": "old",
            "run_inputs": {"implementation_sha256": "old"},
        },
    }))

    result = replay_module.replay(source, target)

    assert result == {"datasets": 1, "refused_rows": 0}
    assert not (source / "data" / "tables" / "page_001_panels.json").exists()
    normalized = json.loads(
        (target / "data" / "tables" / "page_001_panels.json").read_text()
    )
    assert normalized["schema_version"] == 5
    assert normalized["checks"] == {"passed": True, "issues": [], "review_signals": []}
    provenance = json.loads((target / "provenance.json").read_text())
    assert provenance["version"] == 2
    assert provenance["provenance"]["run_inputs"]["postprocessing_replay"] == {
        "source_version": 1,
        "source_provenance_sha256": replay_module._sha256(source / "provenance.json"),
        "scope": "normalized_table_artifacts_and_resolution",
    }
    assert provenance["provenance"]["run_fingerprint"] != "old"
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["document"]["version"] == 2
    assert manifest["representations"]["tables"][0]["normalized_csv"] == (
        "data/tables/page_001_panels.csv"
    )
