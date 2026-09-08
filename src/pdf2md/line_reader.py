"""Prepare and attach conservative PP-OCRv6 evidence for table row keys.

The heavy recognizer runs in a separate environment. This module emits hash-pinned
inputs and later accepts only the benchmarked model at the frozen score threshold.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from PIL import Image

from pdf2md.logging import Progress, get_logger
from pdf2md.panel_keys import (
    _aligned_panel_key_words,
    _key,
    _localized_panel_key_words,
    _panel_key_bounds,
    _row_key_words,
    _service_crop,
)
from pdf2md.render import CropRenderer
from pdf2md.schema import BBox
from pdf2md.table_verify import (
    _aligned_tesseract_lines,
    _column_centers,
    _numeric_source_rows,
    _table_layout,
    typed_value,
)
from pdf2md.tables import RepeatedPanelLayout, gfm_rows

MINIMUM_SCORE = 0.99
PINNED_READER = {
    "model_name": "PP-OCRv6_medium_rec",
    "model_sha256": "00a024b1a2165b852c2b9dab611e0a68fd23f9a28c441c600a04f078a106b5b2",
    "paddleocr_version": "3.7.0",
    "paddlex_version": "3.7.2",
    "paddlepaddle_gpu_version": "3.2.1+fc",
}
log = get_logger("line_reader")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()




def _run_tesseract(executable: str, image_path: Path) -> str:
    completed = subprocess.run(
        [executable, str(image_path), "stdout", "--psm", "6", "-l", "eng", "tsv"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if completed.returncode:
        raise RuntimeError(
            f"Tesseract exited {completed.returncode}: {completed.stderr.strip()}"
        )
    return completed.stdout










def _panel_numeric_source_rows(
    rows: list[list[str]], layout: RepeatedPanelLayout
) -> dict[int, list[int]]:
    return {
        start: [
            row_index for row_index, row in enumerate(rows)
            if start < len(row) and typed_value(row[start])[2] == "numeric"
        ]
        for start in layout.starts
    }




def _line_reader_source_rows(
    rows: list[list[str]], layout: RepeatedPanelLayout
) -> list[int]:
    numeric_keys = [
        row_index for row_index, row in enumerate(rows)
        if all(
            start < len(row) and typed_value(row[start])[2] == "numeric"
            for start in layout.starts
        )
    ]
    return numeric_keys or _numeric_source_rows(rows)


def _layout_family(layout: RepeatedPanelLayout, inherited: bool) -> str:
    if len(layout.starts) == 1:
        return "single_panel"
    suffix = "continuation" if inherited else "header"
    return f"repeated_{len(layout.starts)}_panel_{suffix}"




def _table_crop(
    source_pdf: Path,
    table: dict,
    block: dict,
    crop_path: Path,
) -> None:
    bbox = table.get("bbox") or block.get("bbox")
    if not bbox:
        raise ValueError("table bbox missing")
    with CropRenderer(source_pdf, dpi=300, padding_pts=6.0) as renderer:
        renderer.crop(int(table["page"]), BBox(**bbox), crop_path, dpi=300)


def prepare(
    version_dir: Path,
    output_dir: Path,
    tesseract_executable: str = "tesseract",
    block_ids: set[str] | None = None,
    progress: Progress | None = None,
    *,
    page_from: int | None = None,
    page_to: int | None = None,
) -> dict:
    """Create self-contained reader inputs without exposing primary values to the model."""
    if page_from is not None and page_to is not None and page_from > page_to:
        raise ValueError("page_from must not exceed page_to")
    executable = shutil.which(tesseract_executable)
    if executable is None:
        raise FileNotFoundError(f"Tesseract executable not found: {tesseract_executable}")
    provenance_path = version_dir / "provenance.json"
    provenance = json.loads(provenance_path.read_text())
    source_pdf = version_dir.parent / "source.pdf"
    if _sha256(source_pdf) != provenance["source_sha256"]:
        raise ValueError("stored source PDF hash does not match provenance")

    output_dir.mkdir(parents=True, exist_ok=True)
    source_crop_dir = output_dir / "source-crops"
    input_crop_dir = output_dir / "crops"
    source_crop_dir.mkdir(exist_ok=True)
    input_crop_dir.mkdir(exist_ok=True)
    blocks = {block["id"]: block for block in provenance["blocks"]}
    records = []
    refusals = []
    table_summaries = []
    selected_tables = [
        table for table in provenance["tables"]
        if block_ids is None or table["block_id"] in block_ids
        if page_from is None or int(table["page"]) >= page_from
        if page_to is None or int(table["page"]) <= page_to
    ]
    previous_layout = None
    if selected_tables:
        first_block_id = selected_tables[0]["block_id"]
        for table in provenance["tables"]:
            if table["block_id"] == first_block_id:
                break
            rows = gfm_rows(table.get("gfm") or "")
            if rows:
                previous_layout = _table_layout(rows, previous_layout)
    progress = progress or Progress(log)
    if selected_tables:
        progress.count(
            "preparing line-reader crops", 0, len(selected_tables),
            unit="tables", force=True,
        )

    for completed, table in enumerate(selected_tables, start=1):
        if completed > 1:
            progress.count(
                "preparing line-reader crops", completed - 1, len(selected_tables),
                unit="tables", detail=f"page {table['page']}",
            )
        block_id = table["block_id"]
        table_summary = {
            "source_block_id": block_id,
            "page": int(table["page"]),
            "layout_family": "unavailable",
            "panel_count": 0,
            "panel_widths": [],
            "source_rows": 0,
        }
        table_summaries.append(table_summary)
        rows = gfm_rows(table.get("gfm") or "")
        block = blocks.get(block_id, {})
        stem = block_id.strip("#/").replace("/", "_")
        if not rows:
            refusals.append({"source_block_id": block_id, "reason": "grid_unavailable"})
            continue
        direct_layout = _table_layout(rows, None)
        layout = _table_layout(rows, previous_layout)
        if layout is None:
            refusals.append({"source_block_id": block_id, "reason": "layout_unavailable"})
            continue
        inherited = (
            len(layout.starts) > 1
            and (direct_layout is None or len(direct_layout.starts) == 1)
        )
        previous_layout = layout
        source_rows = _line_reader_source_rows(rows, layout)
        panel_source_rows = {start: list(source_rows) for start in layout.starts}
        if len(layout.starts) > 1:
            numeric_panel_rows = _panel_numeric_source_rows(rows, layout)
            if all(numeric_panel_rows.values()):
                panel_source_rows = numeric_panel_rows
        table_summary.update({
            "layout_family": _layout_family(layout, inherited),
            "panel_count": len(layout.starts),
            "panel_widths": [
                layout.panel_width(index) for index in range(len(layout.starts))
            ],
            "source_rows": max(map(len, panel_source_rows.values()), default=0),
            "panel_source_rows": [
                len(panel_source_rows[start]) for start in layout.starts
            ],
        })
        crop_path = source_crop_dir / f"{stem}.png"
        try:
            _table_crop(source_pdf, table, block, crop_path)
            tsv = _run_tesseract(executable, crop_path)
        except (OSError, RuntimeError, ValueError) as error:
            refusals.append({
                "source_block_id": block_id,
                "reason": f"table_crop_failed:{type(error).__name__}",
            })
            continue
        aligned = dict(_aligned_tesseract_lines(rows, tsv, layout))
        source_crop_sha256 = _sha256(crop_path)
        with Image.open(crop_path) as image:
            panel_bounds = None
            panel_key_words = None
            panel_key_refusals = {}
            locator_method = "aligned_table_row"
            numeric_key_layout = any(panel_source_rows.values()) and all(
                typed_value(rows[row_index][start])[2] == "numeric"
                for start in layout.starts
                for row_index in panel_source_rows[start]
            )
            if numeric_key_layout:
                centers = _column_centers(list(aligned.values()), layout)
                panel_bounds = _panel_key_bounds(layout, centers, image.width)
                if panel_bounds is None:
                    if len(layout.starts) > 1:
                        refusals.append({
                            "source_block_id": block_id,
                            "reason": "panel_geometry_unavailable",
                        })
                        continue
                elif len({tuple(value) for value in panel_source_rows.values()}) == 1:
                    shared_source_rows = next(iter(panel_source_rows.values()))
                    panel_key_words = _aligned_panel_key_words(
                        rows, shared_source_rows, layout, tsv, panel_bounds
                    )
                    if panel_key_words is not None:
                        locator_method = "shared_panel_grid"
                if panel_key_words is None:
                    if len(layout.starts) > 1:
                        panel_key_words, panel_key_refusals = (
                            _localized_panel_key_words(
                                rows, panel_source_rows, layout, tsv, panel_bounds
                            )
                        )
                        if panel_key_words:
                            locator_method = "panel_local_monotonic"
                    else:
                        panel_bounds = None
                if len(layout.starts) > 1 and not panel_key_words:
                    refusals.append({
                        "source_block_id": block_id,
                        "reason": "panel_row_grid_unavailable",
                    })
                    continue
            for panel_index, start in enumerate(layout.starts):
                for row_index in panel_source_rows[start]:
                    line = aligned.get(row_index)
                    width = layout.panel_width(panel_index)
                    panel = rows[row_index][start:start + width]
                    if not any(cell.strip() for cell in panel):
                        continue
                    primary = panel[0].strip() if panel else ""
                    if not primary:
                        refusals.append({
                            "source_block_id": block_id,
                            "source_row": row_index,
                            "source_column": start,
                            "panel": panel_index,
                            "reason": "primary_key_missing",
                        })
                        continue
                    words = (
                        _row_key_words(primary, line or [])
                        if panel_bounds is None
                        else panel_key_words.get((row_index, start), [])
                    )
                    if not words:
                        refusals.append({
                            "source_block_id": block_id,
                            "source_row": row_index,
                            "source_column": start,
                            "panel": panel_index,
                            "primary_value": primary,
                            "reason": panel_key_refusals.get(
                                (row_index, start), "row_key_alignment_missing"
                            ),
                        })
                        continue
                    sample_id = f"{stem}:r{row_index}:c{start}"
                    input_path = input_crop_dir / f"{stem}_r{row_index}_c{start}.png"
                    crop, source_box = _service_crop(image, words)
                    crop.save(input_path)
                    crop.close()
                    records.append({
                        "id": sample_id,
                        "page": int(table["page"]),
                        "source_block_id": block_id,
                        "source_row": row_index,
                        "source_column": start,
                        "panel": panel_index,
                        "locator_method": locator_method,
                        "primary_value": primary,
                        "source_crop": crop_path.relative_to(output_dir).as_posix(),
                        "source_crop_sha256": source_crop_sha256,
                        "source_box": source_box,
                        "crop": input_path.relative_to(output_dir).as_posix(),
                        "crop_sha256": _sha256(input_path),
                    })
    if selected_tables:
        progress.count(
            "preparing line-reader crops", len(selected_tables), len(selected_tables),
            unit="tables",
        )

    prepared_by_table = Counter(record["source_block_id"] for record in records)
    locator_methods_by_table = {
        block_id: dict(Counter(
            record["locator_method"] for record in records
            if record["source_block_id"] == block_id
        ))
        for block_id in prepared_by_table
    }
    refused_by_table = Counter(record["source_block_id"] for record in refusals)
    refusal_reasons_by_table = {
        block_id: dict(Counter(
            record["reason"] for record in refusals
            if record["source_block_id"] == block_id
        ))
        for block_id in refused_by_table
    }
    for summary in table_summaries:
        block_id = summary["source_block_id"]
        expected_key_cells = sum(summary.get("panel_source_rows", [])) or (
            summary["source_rows"] * summary["panel_count"]
        )
        summary.update({
            "expected_key_cells": expected_key_cells,
            "prepared": prepared_by_table[block_id],
            "unprepared_key_cells": max(
                expected_key_cells - prepared_by_table[block_id], 0
            ),
            "preparation_refused": refused_by_table[block_id],
            "refusal_reasons": refusal_reasons_by_table.get(block_id, {}),
            "locator_methods": locator_methods_by_table.get(block_id, {}),
        })

    manifest = {
        "schema_version": 1,
        "method": "panel_aware_table_row_key_line_reader",
        "selection": {
            "block_ids": sorted(block_ids) if block_ids is not None else None,
            "page_from": page_from,
            "page_to": page_to,
        },
        "version_dir": str(version_dir.resolve()),
        "version_provenance_sha256": _sha256(provenance_path),
        "source_sha256": provenance["source_sha256"],
        "minimum_score": MINIMUM_SCORE,
        "pinned_reader": PINNED_READER,
        "records": records,
        "refusals": refusals,
        "tables": table_summaries,
        "expected_key_cells": sum(
            summary["expected_key_cells"] for summary in table_summaries
        ),
        "unprepared_key_cells": sum(
            summary["unprepared_key_cells"] for summary in table_summaries
        ),
        "preparation_refusal_events": len(refusals),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    inputs = {
        "schema_version": 1,
        "records": [
            {key: record[key] for key in ("id", "crop", "crop_sha256")}
            for record in records
        ],
    }
    (output_dir / "inputs.json").write_text(json.dumps(inputs, indent=2) + "\n")
    return manifest


def _validate_reader(reader: dict) -> None:
    mismatches = [
        key for key, expected in PINNED_READER.items()
        if reader.get(key) != expected
    ]
    if mismatches:
        raise ValueError(f"unapproved reader identity: {', '.join(mismatches)}")


def apply(output_dir: Path, run_path: Path) -> dict:
    """Attach reader agreement as a sidecar; extracted table values remain untouched."""
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    version_dir = Path(manifest["version_dir"])
    if _sha256(version_dir / "provenance.json") != manifest["version_provenance_sha256"]:
        raise ValueError("conversion provenance changed after crop preparation")
    run = json.loads(run_path.read_text())
    _validate_reader(run.get("reader", {}))
    results = {record["id"]: record for record in run.get("records", [])}
    if len(results) != len(run.get("records", [])):
        raise ValueError("duplicate reader result id")
    records = []
    for prepared in manifest["records"]:
        crop_path = output_dir / prepared["crop"]
        result = results.get(prepared["id"])
        reader_value = None
        reader_score = None
        refusal = None
        source_crop_path = output_dir / prepared["source_crop"]
        if not source_crop_path.is_file():
            refusal = "source_crop_missing"
        elif _sha256(source_crop_path) != prepared["source_crop_sha256"]:
            refusal = "source_crop_hash_mismatch"
        elif not crop_path.is_file():
            refusal = "prepared_crop_missing"
        elif _sha256(crop_path) != prepared["crop_sha256"]:
            refusal = "prepared_crop_hash_mismatch"
        elif result is None:
            refusal = "result_missing"
        elif result.get("input_sha256") != prepared["crop_sha256"]:
            refusal = "result_input_hash_mismatch"
        elif result.get("error"):
            refusal = "reader_error"
        elif not str(result.get("text", "")).strip():
            refusal = "reader_text_missing"
        elif result.get("score") is None:
            refusal = "reader_score_missing"
        else:
            reader_value = str(result["text"]).strip()
            reader_score = float(result["score"])
            if reader_score < MINIMUM_SCORE:
                refusal = "reader_score_below_threshold"
            elif _key(reader_value) != _key(prepared["primary_value"]):
                refusal = "reader_primary_disagreement"
        records.append({
            "schema_version": 1,
            **prepared,
            "reader": run["reader"]["model_name"],
            "reader_value": reader_value,
            "reader_score": reader_score,
            "verification_status": "reader_agreement" if refusal is None else "reader_refused",
            "reader_refusal_reason": refusal,
        })

    counts = Counter(record["verification_status"] for record in records)
    reader_counts_by_table: dict[str, Counter] = {}
    for record in records:
        block_id = record.get("source_block_id")
        if block_id:
            reader_counts_by_table.setdefault(block_id, Counter()).update(
                [record["verification_status"]]
            )
    table_results = []
    layout_families: dict[str, dict[str, object]] = {}
    for summary in manifest.get("tables", []):
        block_id = summary["source_block_id"]
        reader_counts = reader_counts_by_table.get(block_id, Counter())
        expected_key_cells = summary.get(
            "expected_key_cells", summary["source_rows"] * summary["panel_count"]
        )
        unprepared_key_cells = summary.get(
            "unprepared_key_cells",
            max(expected_key_cells - summary["prepared"], 0),
        )
        table_result = {
            **summary,
            "expected_key_cells": expected_key_cells,
            "unprepared_key_cells": unprepared_key_cells,
            "reader_agreement": reader_counts["reader_agreement"],
            "reader_refused": reader_counts["reader_refused"],
        }
        table_results.append(table_result)
        family = layout_families.setdefault(summary["layout_family"], {
            "tables": 0,
            "expected_key_cells": 0,
            "prepared": 0,
            "unprepared_key_cells": 0,
            "preparation_refused": 0,
            "reader_agreement": 0,
            "reader_refused": 0,
            "preparation_refusal_reasons": Counter(),
            "locator_methods": Counter(),
        })
        family["tables"] += 1
        family["expected_key_cells"] += expected_key_cells
        family["prepared"] += summary["prepared"]
        family["unprepared_key_cells"] += unprepared_key_cells
        family["preparation_refused"] += summary["preparation_refused"]
        family["reader_agreement"] += reader_counts["reader_agreement"]
        family["reader_refused"] += reader_counts["reader_refused"]
        family["preparation_refusal_reasons"].update(summary["refusal_reasons"])
        family["locator_methods"].update(summary.get("locator_methods", {}))
    for family in layout_families.values():
        family["preparation_refusal_reasons"] = dict(
            family["preparation_refusal_reasons"]
        )
        family["locator_methods"] = dict(family["locator_methods"])
    report = {
        "schema_version": 1,
        "method": manifest["method"],
        "contract": {
            "minimum_score": MINIMUM_SCORE,
            "acceptance": "normalized reader equals primary at or above the fixed score",
            "effect": "evidence sidecar only; primary table values are never rewritten",
        },
        "source_sha256": manifest["source_sha256"],
        "version_dir": manifest["version_dir"],
        "selection": manifest.get("selection"),
        "version_provenance_sha256": manifest["version_provenance_sha256"],
        "manifest_sha256": _sha256(manifest_path),
        "run_sha256": _sha256(run_path),
        "reader": run["reader"],
        "prepared": len(records),
        "expected_key_cells": sum(
            table["expected_key_cells"] for table in table_results
        ),
        "unprepared_key_cells": sum(
            table["unprepared_key_cells"] for table in table_results
        ),
        "reader_agreement": counts["reader_agreement"],
        "reader_refused": counts["reader_refused"],
        "locator_methods": dict(Counter(
            record.get("locator_method", "legacy") for record in records
        )),
        "preparation_refused": len(manifest["refusals"]),
        "preparation_refusal_events": len(manifest["refusals"]),
        "refusal_reasons": dict(Counter(
            record["reader_refusal_reason"]
            for record in records if record["reader_refusal_reason"]
        )),
        "preparation_refusals": manifest["refusals"],
        "layout_families": layout_families,
        "tables": table_results,
        "evidence": "evidence.jsonl",
    }
    with (output_dir / "evidence.jsonl").open("w") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
