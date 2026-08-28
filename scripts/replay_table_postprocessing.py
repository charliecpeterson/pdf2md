"""Rebuild table normalization from a completed bundle without rerunning its parser.

The target is a new immutable version. Its provenance pins the source version so a
postprocessing comparison cannot be mistaken for an independent extraction run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from pdf2md.cache import run_fingerprint
from pdf2md.pipeline import _implementation_sha256
from pdf2md.schema import BBox, Block, BlockType, CoverageStatus, TableData
from pdf2md.table_artifacts import _write_normalized_panels
from pdf2md.table_resolution import enrich_normalized_datasets
from pdf2md.tables import gfm_rows


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _version(path: Path) -> int:
    match = re.fullmatch(r"v(\d+)", path.name)
    if match is None:
        raise ValueError(f"version directory must be named v<n>: {path}")
    return int(match.group(1))


def _bbox(value: dict[str, float] | None) -> BBox | None:
    return BBox(**value) if value is not None else None


def _block(value: dict) -> Block:
    return Block(
        id=value["id"],
        type=BlockType(value["type"]),
        text=value["text"],
        page=value["page"],
        bbox=_bbox(value.get("bbox")),
        confidence=value.get("confidence"),
        engine=value.get("engine", "docling"),
        coverage_status=CoverageStatus(value.get("coverage_status", "pending")),
        extra=dict(value.get("extra") or {}),
    )


def _table(value: dict) -> TableData:
    return TableData(
        block_id=value["block_id"],
        page=value["page"],
        bbox=_bbox(value.get("bbox")),
        gfm=value.get("gfm") or "",
        html=value.get("html"),
        has_spanning_cells=value.get("has_spanning_cells", False),
        preformatted=value.get("preformatted"),
        candidate_path=value.get("candidate_path", ""),
        data_path=value.get("data_path", ""),
        json_path=value.get("json_path", ""),
        normalized_data_path=value.get("normalized_data_path", ""),
        normalized_json_path=value.get("normalized_json_path", ""),
        cell_evidence_path=value.get("cell_evidence_path", ""),
        cell_evidence_counts=dict(value.get("cell_evidence_counts") or {}),
        cell_resolution_counts=dict(value.get("cell_resolution_counts") or {}),
    )


def replay(source: Path, target: Path) -> dict[str, int]:
    source = source.resolve()
    target = target.resolve()
    source_provenance_path = source / "provenance.json"
    if not source_provenance_path.is_file():
        raise ValueError(f"source version is incomplete: {source}")
    if target.exists():
        raise ValueError(f"target already exists: {target}")
    if source.parent != target.parent or _version(target) <= _version(source):
        raise ValueError("target must be a later version of the same document")

    started = datetime.now(timezone.utc)
    source_provenance_hash = _sha256(source_provenance_path)
    provenance = json.loads(source_provenance_path.read_text())

    def ignore(path: str, names: list[str]) -> set[str]:
        return {"provenance.json"} if Path(path).resolve() == source else set()

    shutil.copytree(source, target, ignore=ignore)
    blocks = [_block(value) for value in provenance["blocks"]]
    tables = [_table(value) for value in provenance["tables"]]
    previous_datasets = {
        path
        for table in tables
        for path in (table.normalized_data_path, table.normalized_json_path)
        if path
    }
    for table in tables:
        table.normalized_data_path = ""
        table.normalized_json_path = ""

    artifact_rows = {table.block_id: gfm_rows(table.gfm) for table in tables}
    table_dir = target / "data" / "tables"
    _write_normalized_panels(
        tables,
        artifact_rows,
        {block.id: block for block in blocks},
        table_dir,
        target,
    )
    enrich_normalized_datasets(tables, target)

    current_datasets = {
        path
        for table in tables
        for path in (table.normalized_data_path, table.normalized_json_path)
        if path
    }
    for stale in previous_datasets - current_datasets:
        (target / stale).unlink(missing_ok=True)
    for table in tables:
        if not table.json_path:
            continue
        sidecar_path = target / table.json_path
        sidecar = json.loads(sidecar_path.read_text())
        sidecar["normalized_csv"] = table.normalized_data_path or None
        sidecar["normalized_json"] = table.normalized_json_path or None
        sidecar_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n")

    profile_path = target / "profile.json"
    profile = json.loads(profile_path.read_text())
    if profile.get("derived_table_datasets") != len(current_datasets) // 2:
        raise ValueError("replay changed the dataset count; regenerate profile and README")

    table_by_id = {table.block_id: table for table in tables}
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["document"]["version"] = _version(target)
    for record in manifest["representations"]["tables"]:
        table = table_by_id[record["block_id"]]
        record["normalized_csv"] = table.normalized_data_path or None
        record["normalized_json"] = table.normalized_json_path or None
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")

    finished = datetime.now(timezone.utc)
    run_inputs = dict(provenance["provenance"].get("run_inputs") or {})
    run_inputs["implementation_sha256"] = _implementation_sha256()
    run_inputs["postprocessing_replay"] = {
        "source_version": _version(source),
        "source_provenance_sha256": source_provenance_hash,
        "scope": "normalized_table_artifacts_and_resolution",
    }
    provenance["version"] = _version(target)
    provenance["tables"] = [asdict(table) for table in tables]
    provenance_record = provenance["provenance"]
    provenance_record["started_at"] = started.isoformat()
    provenance_record["finished_at"] = finished.isoformat()
    provenance_record["duration_s"] = round((finished - started).total_seconds(), 2)
    provenance_record["run_inputs"] = run_inputs
    provenance_record["run_fingerprint"] = run_fingerprint(run_inputs)
    pending = target / "provenance.json.tmp"
    pending.write_text(json.dumps(provenance, indent=2, ensure_ascii=False) + "\n")
    pending.replace(target / "provenance.json")

    refusal_count = 0
    for path in current_datasets:
        if not path.endswith(".json"):
            continue
        dataset = json.loads((target / path).read_text())
        refusal_count += sum(
            issue.get("kind") == "panel_row_refused"
            for issue in dataset.get("checks", {}).get("issues", [])
        )
    return {"datasets": len(current_datasets) // 2, "refused_rows": refusal_count}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    result = replay(args.source, args.target)
    print(
        f"replayed {result['datasets']} normalized datasets; "
        f"{result['refused_rows']} structurally ambiguous rows refused"
    )


if __name__ == "__main__":
    main()
