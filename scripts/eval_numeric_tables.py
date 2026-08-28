"""Compare extracted numeric-table cells with independently checked source labels.

Reports exact string agreement, disagreement, and cells the converter refused to emit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

from pdf2md.table_verify import (
    _is_number,
    _looks_numeric,
    _numeric_read,
    _table_layout,
    _word_lines,
    map_tesseract_tsv as _map_tesseract_tsv,
    numeric_values_equal,
    typed_value,
)
from pdf2md.tables import RepeatedPanelLayout, gfm_rows, html_tables, split_repeated_panels

_LABELS = Path(__file__).parent.parent / "tests" / "numeric_table_labels.json"
_NUMERIC_TOKEN = re.compile(
    r"(?<![\w.])[-+−–—]?(?:\d+(?:[ ,]\d{3})*(?:\.\d+(?: \d{1,3})*)?|\.\d+)"
    r"(?:[eE][-+]?\d+)?(?![\w.])"
)


def _values_equal(actual: str, expected: str) -> bool:
    _, actual_numeric, actual_status = typed_value(actual)
    _, expected_numeric, expected_status = typed_value(expected)
    if actual_status == expected_status == "numeric":
        return numeric_values_equal(actual_numeric, expected_numeric)
    return actual == expected


def _latest_version(out_dir: Path, source_sha256: str) -> Path | None:
    document_dir = out_dir / source_sha256[:16]
    versions = sorted(
        document_dir.glob("v*"),
        key=lambda path: int(path.name[1:]) if path.name[1:].isdigit() else -1,
        reverse=True,
    )
    return next((path for path in versions if (path / "provenance.json").is_file()), None)


def _version_dir(out_dir: Path, document: dict) -> Path | None:
    version = document.get("version")
    if version is None:
        return _latest_version(out_dir, document["source_sha256"])
    path = out_dir / document["source_sha256"][:16] / version
    return path if (path / "provenance.json").is_file() else None


def _candidate_tables(version_dir: Path) -> dict[str, list[list[str]]]:
    tables: dict[str, list[list[str]]] = {}
    manifest_path = version_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        for item in manifest.get("representations", {}).get("tables", []):
            relative = item.get("json")
            if not relative or not (version_dir / relative).is_file():
                continue
            record = json.loads((version_dir / relative).read_text())
            tables[item["block_id"]] = record.get("rows") or []
    if tables:
        return tables

    provenance = json.loads((version_dir / "provenance.json").read_text())
    for table in provenance.get("tables", []):
        tables[table["block_id"]] = gfm_rows(table.get("gfm") or "")
    return tables


def _table_crops(version_dir: Path) -> dict[str, Path]:
    manifest_path = version_dir / "manifest.json"
    if not manifest_path.is_file():
        return {}
    manifest = json.loads(manifest_path.read_text())
    return {
        item["block_id"]: version_dir / item["crop"]
        for item in manifest.get("representations", {}).get("tables", [])
        if item.get("crop")
    }


def _table_pages(version_dir: Path) -> dict[str, int]:
    manifest_path = version_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        pages = {
            item["block_id"]: item["page"]
            for item in manifest.get("representations", {}).get("tables", [])
        }
        if pages:
            return pages
    provenance = json.loads((version_dir / "provenance.json").read_text())
    return {table["block_id"]: table["page"] for table in provenance.get("tables", [])}


def _resolved_cells(version_dir: Path) -> dict[tuple[str, int, int], dict]:
    resolved = {}
    for path in (version_dir / "data" / "tables").glob("*.cells.jsonl"):
        for line in path.read_text().splitlines():
            record = json.loads(line)
            if "best_value" not in record:
                continue
            resolved[(
                record["source_block_id"],
                record["source_row"],
                record["source_column"],
            )] = record
    return resolved


def _cell_evidence_reference(
    out_dir: Path,
    labels: dict,
) -> tuple[
    dict[tuple[str, str, int, int], str],
    dict[tuple[str, str, int, int], str],
    dict,
]:
    readings = {}
    refusals = {}
    readers = set()
    digest = hashlib.sha256()
    evidence_files = 0
    for document in labels.get("documents", []):
        source_sha256 = document["source_sha256"]
        version_dir = _version_dir(out_dir, document)
        evidence = _resolved_cells(version_dir) if version_dir else {}
        if version_dir is not None:
            for path in sorted((version_dir / "data" / "tables").glob("*.cells.jsonl")):
                digest.update(path.name.encode())
                digest.update(b"\0")
                digest.update(path.read_bytes())
                digest.update(b"\0")
                evidence_files += 1
        for cell in document.get("cells", []):
            key = (source_sha256, cell["block_id"], cell["row"], cell["column"])
            record = evidence.get((cell["block_id"], cell["row"], cell["column"]))
            reader_value = record.get("reader_value") if record else None
            if record and record.get("reader"):
                readers.add(str(record["reader"]))
            if reader_value is not None:
                readings[key] = str(reader_value)
            else:
                refusals[key] = (
                    str(record.get("reader_refusal_reason") or "reader_value_missing")
                    if record else "cell_evidence_missing"
                )
    return readings, refusals, {
        "readers": sorted(readers),
        "evidence_files": evidence_files,
        "evidence_sha256": digest.hexdigest(),
    }


def _tesseract_reference(
    version_dir: Path,
    tables: dict[str, list[list[str]]],
    cells: list[dict],
    executable: str,
) -> tuple[dict[tuple[str, int, int], str], list[str]]:
    wanted = {cell["block_id"] for cell in cells}
    crops = _table_crops(version_dir)
    layouts: dict[int, RepeatedPanelLayout] = {}
    block_layouts = {}
    pages = _table_pages(version_dir)
    for block_id, rows in tables.items():
        page = pages.get(block_id)
        if page is None:
            continue
        layout = _table_layout(rows, layouts.get(page))
        if layout is not None:
            layouts[page] = layout
            block_layouts[block_id] = layout

    readings = {}
    refused = []
    for block_id in wanted:
        crop = crops.get(block_id)
        layout = block_layouts.get(block_id)
        if crop is None or not crop.is_file() or layout is None:
            refused.append(block_id)
            continue
        completed = subprocess.run(
            [executable, str(crop), "stdout", "--psm", "6", "-l", "eng", "tsv"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode:
            refused.append(block_id)
            continue
        mapped = _map_tesseract_tsv(tables[block_id], completed.stdout, layout)
        if mapped is None:
            refused.append(block_id)
            continue
        for (row, column), value in mapped.items():
            readings[block_id, row, column] = value
    return readings, refused


def _paddle_tables(response: dict) -> list[list[list[str]]]:
    tables = []
    if response.get("errorCode") != 0:
        return tables
    for page in (response.get("result") or {}).get("layoutParsingResults", []):
        pruned = page.get("prunedResult") or {}
        blocks = pruned.get("parsing_res_list", []) if isinstance(pruned, dict) else pruned
        for block in blocks:
            if block.get("block_label") == "table":
                tables.extend(html_tables(block.get("block_content", "")))
    return tables


def _structure_equal(left: str, right: str) -> bool:
    if _values_equal(left, right):
        return True
    normalize = lambda value: re.sub(r"\s+", "", value).casefold()
    return normalize(left) == normalize(right)


def _paddle_cell(
    source: list[list[str]],
    paddle: list[list[str]],
    row: int,
    column: int,
) -> tuple[str | None, str | None]:
    if row >= len(source) or column >= len(source[row]):
        return None, "source_cell_missing"
    if row >= len(paddle) or column >= len(paddle[row]):
        return None, "paddle_cell_missing"

    _, layout = split_repeated_panels(source)
    starts = layout.starts if layout is not None else (0,)
    panel_start = max((start for start in starts if start <= column), default=0)
    if panel_start >= len(source[row]) or panel_start >= len(paddle[row]):
        return None, "panel_row_key_missing"
    if not _structure_equal(source[row][panel_start], paddle[row][panel_start]):
        return None, "panel_row_key_mismatch"

    header_row = 1
    if (
        header_row >= len(source)
        or column >= len(source[header_row])
        or header_row >= len(paddle)
        or column >= len(paddle[header_row])
    ):
        return None, "column_header_missing"
    if not _structure_equal(source[header_row][column], paddle[header_row][column]):
        return None, "column_header_mismatch"
    return paddle[row][column], None


def _paddle_reference(
    out_dir: Path,
    labels: dict,
    manifest_path: Path,
) -> tuple[
    dict[tuple[str, str, int, int], str],
    dict[tuple[str, str, int, int], str],
    dict,
]:
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != 1:
        raise ValueError(f"{manifest_path}: unsupported schema_version")
    entries = {
        (entry["source_sha256"], entry["block_id"]): entry
        for entry in manifest.get("results", [])
    }
    readings = {}
    refusals = {}
    for document in labels.get("documents", []):
        source_sha256 = document["source_sha256"]
        version_dir = _version_dir(out_dir, document)
        source_tables = _candidate_tables(version_dir) if version_dir else {}
        cells_by_block = defaultdict(list)
        for cell in document.get("cells", []):
            cells_by_block[cell["block_id"]].append(cell)
        for block_id, cells in cells_by_block.items():
            entry = entries.get((source_sha256, block_id))
            source = source_tables.get(block_id)
            response_path = (
                manifest_path.parent / entry["response"]
                if entry is not None else None
            )
            paddle = None
            refusal = None
            if entry is None:
                refusal = "manifest_entry_missing"
            elif source is None:
                refusal = "source_table_missing"
            elif response_path is None or not response_path.is_file():
                refusal = "response_missing"
            else:
                tables = _paddle_tables(json.loads(response_path.read_text()))
                table_index = entry.get("table_index")
                if table_index is None and len(tables) != 1:
                    refusal = "ambiguous_paddle_tables"
                elif table_index is not None and not 0 <= table_index < len(tables):
                    refusal = "paddle_table_index_missing"
                else:
                    paddle = tables[table_index or 0]
            for cell in cells:
                key = (source_sha256, block_id, cell["row"], cell["column"])
                if refusal is not None or source is None or paddle is None:
                    refusals[key] = refusal or "paddle_table_missing"
                    continue
                value, cell_refusal = _paddle_cell(
                    source, paddle, cell["row"], cell["column"]
                )
                if value is None:
                    refusals[key] = cell_refusal or "paddle_cell_missing"
                else:
                    readings[key] = value
    return readings, refusals, manifest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ocrflux_reference(
    labels: dict,
    manifest_path: Path,
) -> tuple[
    dict[tuple[str, str, int, int], str],
    dict[tuple[str, str, int, int], str],
    dict,
]:
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != 1:
        raise ValueError(f"{manifest_path}: unsupported schema_version")
    entries = {
        (entry["source_sha256"], entry["block_id"]): entry
        for entry in manifest.get("results", [])
    }
    readings = {}
    refusals = {}
    for document in labels.get("documents", []):
        source_sha256 = document["source_sha256"]
        for cell in document.get("cells", []):
            block_id = cell["block_id"]
            key = (source_sha256, block_id, cell["row"], cell["column"])
            entry = entries.get((source_sha256, block_id))
            if entry is None:
                refusals[key] = "manifest_entry_missing"
                continue
            markdown_path = manifest_path.parent / entry["markdown"]
            if not markdown_path.is_file():
                refusals[key] = "markdown_missing"
                continue
            if entry.get("markdown_sha256") != _sha256(markdown_path):
                refusals[key] = "markdown_hash_mismatch"
                continue
            tables = html_tables(markdown_path.read_text())
            table_index = entry.get("table_index", 0)
            if not 0 <= table_index < len(tables):
                refusals[key] = "ocrflux_table_missing"
                continue
            table = tables[table_index]
            shape = entry.get("reference_shape")
            actual_shape = [len(table), max((len(row) for row in table), default=0)]
            if shape is not None and shape != actual_shape:
                refusals[key] = "reference_shape_mismatch"
                continue
            row_map = entry.get("row_map") or {}
            column_map = entry.get("column_map") or {}
            row = row_map.get(str(cell["row"]), cell["row"])
            column = column_map.get(str(cell["column"]), cell["column"])
            if row >= len(table) or column >= len(table[row]):
                refusals[key] = "ocrflux_cell_missing"
                continue
            readings[key] = table[row][column]
    return readings, refusals, manifest


def _paddle_numeric_read(response: dict) -> tuple[str | None, str | None]:
    if response.get("errorCode") != 0:
        return None, "paddle_service_error"
    contents = []
    for page in (response.get("result") or {}).get("layoutParsingResults", []):
        pruned = page.get("prunedResult") or {}
        blocks = pruned.get("parsing_res_list", []) if isinstance(pruned, dict) else pruned
        contents.extend(
            str(block.get("block_content", ""))
            for block in blocks
            if block.get("block_content")
        )
    readings = [
        match.group(0).strip()
        for content in contents
        for match in _NUMERIC_TOKEN.finditer(content)
    ]
    if not readings:
        return None, "numeric_read_missing"
    if len(readings) != 1:
        return None, "ambiguous_numeric_read"
    return readings[0], None


def _paddle_cell_reference(
    labels: dict,
    run_path: Path,
) -> tuple[
    dict[tuple[str, str, int, int], str],
    dict[tuple[str, str, int, int], str],
    dict,
]:
    run = json.loads(run_path.read_text())
    if run.get("schema_version") != 1:
        raise ValueError(f"{run_path}: unsupported schema_version")
    entries = {
        (
            entry["source_sha256"], entry["block_id"],
            entry["source_row"], entry["source_column"],
        ): entry
        for entry in run.get("results", [])
    }
    crop_refusals = {}
    source_manifest = run_path.parent / run.get("source_manifest", "")
    if source_manifest.is_file():
        crop_manifest = json.loads(source_manifest.read_text())
        crop_refusals = {
            (
                entry["source_sha256"], entry["block_id"],
                entry["row"], entry["column"],
            ): entry["reason"]
            for entry in crop_manifest.get("refusals", [])
        }

    readings = {}
    refusals = {}
    for document in labels.get("documents", []):
        source_sha256 = document["source_sha256"]
        for cell in document.get("cells", []):
            key = (
                source_sha256, cell["block_id"], cell["row"], cell["column"]
            )
            entry = entries.get(key)
            if entry is None:
                refusals[key] = crop_refusals.get(key, "run_entry_missing")
                continue
            response_path = run_path.parent / entry["response"]
            if not response_path.is_file():
                refusals[key] = "response_missing"
                continue
            if entry.get("response_sha256") != _sha256(response_path):
                refusals[key] = "response_hash_mismatch"
                continue
            if entry.get("http_status") != 200 or entry.get("error_code") != 0:
                refusals[key] = "paddle_request_failed"
                continue
            reading, refusal = _paddle_numeric_read(json.loads(response_path.read_text()))
            if reading is None:
                refusals[key] = refusal or "numeric_read_missing"
            else:
                readings[key] = reading
    return readings, refusals, run


def evaluate(
    out_dir: Path,
    labels: dict,
    reference: dict[tuple[str, str, int, int], str] | None = None,
    reference_refusals: dict[tuple[str, str, int, int], str] | None = None,
) -> dict:
    records = []
    for document in labels.get("documents", []):
        source_sha256 = document["source_sha256"]
        version_dir = _version_dir(out_dir, document)
        tables = _candidate_tables(version_dir) if version_dir else {}
        resolved = _resolved_cells(version_dir) if version_dir else {}
        for cell in document.get("cells", []):
            table = tables.get(cell["block_id"])
            row = cell["row"]
            column = cell["column"]
            actual = None
            if table is not None and row < len(table) and column < len(table[row]):
                actual = table[row][column]
            if actual is None:
                outcome = "tool_refused"
            elif _values_equal(actual, cell["expected"]):
                outcome = "agree"
            else:
                outcome = "disagree"
            record = {
                "source_sha256": source_sha256,
                "page": cell["page"],
                "block_id": cell["block_id"],
                "row": row,
                "column": column,
                "label": cell.get("label"),
                "expected": cell["expected"],
                "actual": actual,
                "outcome": outcome,
            }
            resolution = resolved.get((cell["block_id"], row, column))
            if resolution is not None:
                best_actual = resolution["best_value"]
                record.update({
                    "best_actual": best_actual,
                    "best_outcome": (
                        "agree" if _values_equal(best_actual, cell["expected"])
                        else "disagree"
                    ),
                    "confidence": resolution["confidence"],
                    "resolution_basis": resolution["resolution_basis"],
                })
            if reference is not None:
                reference_key = (source_sha256, cell["block_id"], row, column)
                reference_actual = reference.get(reference_key)
                if reference_actual is None:
                    reference_outcome = "tool_refused"
                elif _values_equal(reference_actual, cell["expected"]):
                    reference_outcome = "agree"
                else:
                    reference_outcome = "disagree"
                record.update({
                    "reference_actual": reference_actual,
                    "reference_outcome": reference_outcome,
                    "readers_agree": (
                        actual is not None
                        and reference_actual is not None
                        and _values_equal(actual, reference_actual)
                    ),
                })
                if reference_actual is None and reference_refusals is not None:
                    record["reference_refusal_reason"] = reference_refusals.get(
                        reference_key, "unmapped_cell"
                    )
            records.append(record)
    counts = {name: sum(record["outcome"] == name for record in records)
              for name in ("agree", "disagree", "tool_refused")}
    report = {
        "schema_version": 2,
        "checked": len(records),
        **counts,
        "records": records,
    }
    resolved_records = [record for record in records if "best_outcome" in record]
    if resolved_records:
        report["resolved"] = {
            "checked": len(resolved_records),
            "agree": sum(record["best_outcome"] == "agree" for record in resolved_records),
            "disagree": sum(
                record["best_outcome"] == "disagree" for record in resolved_records
            ),
            "changed": sum(
                not _values_equal(record["best_actual"], record["actual"])
                for record in resolved_records
            ),
        }
        report["confidence_calibration"] = {
            confidence: {
                "checked": len(group),
                "agree": sum(record["best_outcome"] == "agree" for record in group),
                "disagree": sum(
                    record["best_outcome"] == "disagree" for record in group
                ),
            }
            for confidence in ("high", "medium", "low")
            if (group := [
                record for record in resolved_records
                if record.get("confidence") == confidence
            ])
        }
    if reference is not None:
        report["reference"] = {
            name: sum(record["reference_outcome"] == name for record in records)
            for name in ("agree", "disagree", "tool_refused")
        }
        comparable = [
            record for record in records
            if record["actual"] is not None and record["reference_actual"] is not None
        ]
        true_positive = sum(
            record["outcome"] == "disagree" and not record["readers_agree"]
            for record in comparable
        )
        false_positive = sum(
            record["outcome"] == "agree" and not record["readers_agree"]
            for record in comparable
        )
        false_negative = sum(
            record["outcome"] == "disagree" and record["readers_agree"]
            for record in comparable
        )
        true_negative = sum(
            record["outcome"] == "agree" and record["readers_agree"]
            for record in comparable
        )
        report["disagreement_detection"] = {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "true_negative": true_negative,
            "precision": round(
                true_positive / (true_positive + false_positive), 4
            ) if true_positive + false_positive else None,
            "recall": round(
                true_positive / (true_positive + false_negative), 4
            ) if true_positive + false_negative else None,
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare extracted numeric-table cells with source-checked labels."
    )
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--labels", type=Path, default=_LABELS)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit nonzero unless every labelled cell agrees.",
    )
    parser.add_argument("--check", action="store_true", help=argparse.SUPPRESS)
    reference_group = parser.add_mutually_exclusive_group()
    reference_group.add_argument(
        "--tesseract",
        nargs="?",
        const="tesseract",
        metavar="PATH",
        help="Compare labelled cells with an independent Tesseract 5 TSV read.",
    )
    reference_group.add_argument(
        "--paddle-manifest",
        type=Path,
        help="Compare labelled cells with preserved PaddleOCR-VL service responses.",
    )
    reference_group.add_argument(
        "--paddle-cell-run",
        type=Path,
        help="Compare labelled cells with isolated-cell PaddleOCR-VL responses.",
    )
    reference_group.add_argument(
        "--ocrflux-manifest",
        type=Path,
        help="Compare labelled cells with pinned OCRFlux table markdown.",
    )
    reference_group.add_argument(
        "--cell-evidence-reader",
        action="store_true",
        help="Compare with the independent-reader values preserved by conversion.",
    )
    args = parser.parse_args()

    labels = json.loads(args.labels.read_text())
    reference = None
    reference_refusals = None
    reference_info = None
    if args.tesseract:
        executable = shutil.which(args.tesseract)
        if executable is None:
            print(f"Tesseract reference unavailable: {args.tesseract}")
            raise SystemExit(2)
        reference = {}
        refused = []
        for document in labels.get("documents", []):
            version_dir = _version_dir(args.out_dir, document)
            if version_dir is None:
                refused.extend(cell["block_id"] for cell in document.get("cells", []))
                continue
            tables = _candidate_tables(version_dir)
            readings, block_refused = _tesseract_reference(
                version_dir, tables, document.get("cells", []), executable
            )
            reference.update({
                (document["source_sha256"], block_id, row, column): value
                for (block_id, row, column), value in readings.items()
            })
            refused.extend(block_refused)
        version = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, check=False
        ).stdout.splitlines()[0]
        reference_info = {
            "name": "tesseract",
            "executable": executable,
            "version": version,
            "refused_blocks": sorted(set(refused)),
        }
    elif args.paddle_manifest:
        reference, reference_refusals, paddle_manifest = _paddle_reference(
            args.out_dir, labels, args.paddle_manifest
        )
        reference_info = {
            "name": "paddleocr-vl",
            "manifest": str(args.paddle_manifest),
            "tool": paddle_manifest.get("tool") or {},
        }
    elif args.paddle_cell_run:
        reference, reference_refusals, paddle_run = _paddle_cell_reference(
            labels, args.paddle_cell_run
        )
        reference_info = {
            "name": "paddleocr-vl-cell",
            "manifest": str(args.paddle_cell_run),
            "url": paddle_run.get("url"),
            "tool": paddle_run.get("tool") or {},
        }
    elif args.ocrflux_manifest:
        reference, reference_refusals, ocrflux_manifest = _ocrflux_reference(
            labels, args.ocrflux_manifest
        )
        reference_info = {
            "name": "ocrflux",
            "manifest": str(args.ocrflux_manifest),
            "tool": ocrflux_manifest.get("tool") or {},
        }
    elif args.cell_evidence_reader:
        reference, reference_refusals, evidence_info = _cell_evidence_reference(
            args.out_dir, labels
        )
        reference_info = {
            "name": "conversion-cell-evidence",
            **evidence_info,
        }

    report = evaluate(args.out_dir, labels, reference, reference_refusals)
    if reference_info is not None:
        report["reference"].update(reference_info)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"numeric cells: {report['agree']}/{report['checked']} agree, "
        f"{report['disagree']} disagree, {report['tool_refused']} tool-refused"
    )
    if "resolved" in report:
        resolved_counts = report["resolved"]
        print(
            f"best values: {resolved_counts['agree']}/{resolved_counts['checked']} agree, "
            f"{resolved_counts['disagree']} disagree, "
            f"{resolved_counts['changed']} changed from primary"
        )
    if reference is not None:
        counts = report["reference"]
        reference_name = reference_info["name"] if reference_info else "reference"
        print(
            f"{reference_name} cells: {counts['agree']}/{report['checked']} agree, "
            f"{counts['disagree']} disagree, {counts['tool_refused']} tool-refused"
        )
        detection = report["disagreement_detection"]
        print(
            "Reader-disagreement error detection: "
            f"precision {detection['precision']}, recall {detection['recall']} "
            f"(TP {detection['true_positive']}, FP {detection['false_positive']}, "
            f"FN {detection['false_negative']}, TN {detection['true_negative']})"
        )
    for record in report["records"]:
        if record["outcome"] != "agree" or record.get("reference_outcome") != "agree":
            print(
                f"  {record['outcome']}: page {record['page']} {record['block_id']} "
                f"[{record['row']},{record['column']}] expected {record['expected']!r}, "
                f"got {record['actual']!r}"
                + (
                    f", {reference_name} {record['reference_outcome']} "
                    f"{record['reference_actual']!r}"
                    if reference is not None else ""
                )
            )
    if (args.strict or args.check) and (
        not report["checked"] or report["disagree"] or report["tool_refused"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
