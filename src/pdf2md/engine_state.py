"""Serialize the engine-neutral parse result used by later enrichment runs.

The stored state sits before pdf2md's post-processing stages. Loading it through
`StoredEngine` lets the normal pipeline rebuild a new immutable bundle without
running the layout engine again.
"""

from __future__ import annotations

from dataclasses import asdict, fields
import hashlib
import json
from pathlib import Path
from typing import Any

from pdf2md.engines.base import EngineResult
from pdf2md.schema import (
    BBox,
    Block,
    BlockType,
    CoverageStatus,
    Digitization,
    FigureLabels,
    FigureRef,
    RawCell,
    RawTable,
    TableData,
)


ENGINE_STATE_SCHEMA_VERSION = 1
ENGINE_STATE_NAME = "base-state.json"


def _known(cls, value: dict[str, Any]) -> dict[str, Any]:
    names = {item.name for item in fields(cls)}
    return {key: item for key, item in value.items() if key in names}


def _bbox(value: dict[str, float] | None) -> BBox | None:
    return BBox(**value) if value else None


def _block(value: dict[str, Any]) -> Block:
    values = _known(Block, value)
    values["type"] = BlockType(values["type"])
    values["coverage_status"] = CoverageStatus(
        values.get("coverage_status", CoverageStatus.PENDING)
    )
    values["bbox"] = _bbox(values.get("bbox"))
    return Block(**values)


def _table(value: dict[str, Any]) -> TableData:
    values = _known(TableData, value)
    values["bbox"] = _bbox(values.get("bbox"))
    return TableData(**values)


def _figure(value: dict[str, Any]) -> FigureRef:
    values = _known(FigureRef, value)
    values["bbox"] = _bbox(values.get("bbox"))
    values["caption_bbox"] = _bbox(values.get("caption_bbox"))
    if values.get("digitization"):
        values["digitization"] = Digitization(
            **_known(Digitization, values["digitization"])
        )
    if values.get("labels"):
        values["labels"] = FigureLabels(**_known(FigureLabels, values["labels"]))
    return FigureRef(**values)


def _raw_table(value: dict[str, Any]) -> RawTable:
    return RawTable(
        cells=[
            RawCell(**{**_known(RawCell, cell), "bbox": _bbox(cell.get("bbox"))})
            for cell in value.get("cells", [])
        ],
        num_rows=int(value.get("num_rows", 0)),
        num_cols=int(value.get("num_cols", 0)),
    )


def write_engine_state(
    version_dir: Path,
    source_sha256: str,
    engine_result: EngineResult,
) -> Path:
    payload = {
        "schema_version": ENGINE_STATE_SCHEMA_VERSION,
        "source_sha256": source_sha256,
        "blocks": [asdict(block) for block in engine_result.blocks],
        "tables": [asdict(table) for table in engine_result.tables],
        "figures": [asdict(figure) for figure in engine_result.figures],
        "page_sizes": {
            str(page): list(size) for page, size in engine_result.page_sizes.items()
        },
        "engine_versions": engine_result.engine_versions,
        "quality_evidence": engine_result.quality_evidence,
        "raw_tables": {
            block_id: asdict(table)
            for block_id, table in engine_result.raw_tables.items()
        },
    }
    version_dir.mkdir(parents=True, exist_ok=True)
    path = version_dir / ENGINE_STATE_NAME
    pending = path.with_suffix(".json.tmp")
    pending.write_text(json.dumps(payload, indent=2, default=str))
    pending.replace(path)
    return path


def load_engine_state(version_dir: Path) -> EngineResult:
    version_dir = Path(version_dir)
    state_path = version_dir / ENGINE_STATE_NAME
    if state_path.is_file():
        raw = json.loads(state_path.read_text())
        if raw.get("schema_version") != ENGINE_STATE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported engine state schema in {state_path}: "
                f"{raw.get('schema_version')}"
            )
    else:
        provenance_path = version_dir / "provenance.json"
        if not provenance_path.is_file():
            raise ValueError(f"completed bundle not found: {version_dir}")
        raw = json.loads(provenance_path.read_text())
        raw.setdefault("page_sizes", {})
        raw.setdefault("engine_versions", (raw.get("provenance") or {}).get("engine_versions", {}))
        raw.setdefault("quality_evidence", {})
        raw.setdefault("raw_tables", {})

    return EngineResult(
        blocks=[_block(value) for value in raw.get("blocks", [])],
        tables=[_table(value) for value in raw.get("tables", [])],
        figures=[_figure(value) for value in raw.get("figures", [])],
        page_sizes={
            int(page): (float(size[0]), float(size[1]))
            for page, size in raw.get("page_sizes", {}).items()
        },
        engine_versions=dict(raw.get("engine_versions") or {}),
        quality_evidence=dict(raw.get("quality_evidence") or {}),
        raw_tables={
            block_id: _raw_table(value)
            for block_id, value in raw.get("raw_tables", {}).items()
        },
    )


def state_sha256(version_dir: Path) -> str:
    state = Path(version_dir) / ENGINE_STATE_NAME
    source = state if state.is_file() else Path(version_dir) / "provenance.json"
    return hashlib.sha256(source.read_bytes()).hexdigest()


class StoredEngine:
    name = "stored"

    def __init__(self, version_dir: Path, stages: tuple[str, ...]) -> None:
        self.version_dir = Path(version_dir).resolve()
        self.stages = tuple(stages)
        provenance_path = self.version_dir / "provenance.json"
        provenance_hash = hashlib.sha256(provenance_path.read_bytes()).hexdigest()
        self.derivation = {
            "kind": "enrichment",
            "source_version": self.version_dir.name,
            "source_provenance_sha256": provenance_hash,
            "base_state_sha256": state_sha256(self.version_dir),
            "stages": list(self.stages),
        }

    def cache_identity(self) -> str:
        return json.dumps(self.derivation, sort_keys=True, separators=(",", ":"))

    def convert(self, pdf_path: Path) -> EngineResult:
        result = load_engine_state(self.version_dir)
        if result.page_sizes:
            return result

        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            result.page_sizes = {
                page + 1: tuple(float(value) for value in pdf[page].get_size())
                for page in range(len(pdf))
            }
        finally:
            pdf.close()
        return result
