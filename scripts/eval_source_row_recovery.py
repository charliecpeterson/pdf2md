"""Re-read structurally damaged table rows directly from their source-panel pixels.

Recovered values remain an evidence overlay. They never replace the conversion's
grid. The OCR-box crop remains first, with an independent projection crop used only
when the original semantic gate refuses.
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from pdf2md.line_reader import (
    MINIMUM_SCORE,
    PINNED_READER,
    _panel_key_bounds,
    _run_tesseract,
    _sha256,
    _table_crop,
    _validate_reader,
    _words_in_bounds,
)
from pdf2md.row_locator import (
    projection_cell_box,
    projection_column_runs,
    projection_panel_bounds,
    projection_row_bands,
)
from pdf2md.table_verify import (
    _aligned_tesseract_lines,
    _column_centers,
    _numeric_read,
    _table_layout,
    _word_lines,
    numeric_values_equal,
    typed_value,
)
from pdf2md.tables import RepeatedPanelLayout, gfm_rows, split_repeated_panels


ONE_GAP_MINIMUM_EXACT_RATIO = 0.85


def _numeric(value: str) -> Decimal | None:
    try:
        return Decimal(_numeric_read(value))
    except InvalidOperation:
        return None


def _strict_numeric_sequence(panel: dict) -> tuple[str, ...] | None:
    sequence = []
    for row in panel["rows"]:
        value = _numeric(row[0]) if row else None
        if value is None:
            return None
        sequence.append(value)
    if not sequence or any(right <= left for left, right in zip(sequence, sequence[1:])):
        return None
    return tuple(str(value) for value in sequence)


def _canonical_sequence(tables: list[dict]) -> tuple[str, ...]:
    occurrences: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for table in tables:
        rows = gfm_rows(table.get("gfm") or "")
        layout = _table_layout(rows, None)
        if layout is None:
            continue
        for panel_index, start in enumerate(layout.starts):
            width = layout.panel_width(panel_index)
            panel = {
                "rows": [
                    row[start:start + width]
                    for row in rows
                    if start < len(row) and typed_value(row[start])[2] == "numeric"
                ]
            }
            sequence = _strict_numeric_sequence(panel)
            if sequence is not None:
                occurrences[sequence].add(table["block_id"])
    repeated = [
        (sequence, blocks)
        for sequence, blocks in occurrences.items()
        if len(blocks) >= 2
    ]
    if not repeated:
        raise ValueError("no numeric row-key sequence repeats across source blocks")
    sequence, _ = max(repeated, key=lambda item: (len(item[0]), len(item[1])))
    return sequence


def _recovery_start(panel: dict, canonical: tuple[str, ...]) -> int | None:
    rows = panel["rows"]
    for position, expected in enumerate(canonical):
        if position >= len(rows):
            return position
        row = rows[position]
        actual = _numeric(row[0]) if row else None
        if actual != Decimal(expected):
            return max(position - 1, 0) if position and actual is not None else position
        if position + 1 < len(rows) and not any(cell.strip() for cell in row[1:]):
            return position
    return len(rows) if len(rows) < len(canonical) else None


def _control_positions(stop: int, maximum: int = 8) -> list[int]:
    if stop <= 0:
        return []
    if stop <= maximum:
        return list(range(stop))
    return sorted({round(index * (stop - 1) / (maximum - 1)) for index in range(maximum)})


def _column_bounds(
    centers: list[tuple[int, float]],
    layout: RepeatedPanelLayout,
    column: int,
    image_width: int,
) -> tuple[int, int] | None:
    panel_index = max(
        (index for index, start in enumerate(layout.starts) if start <= column),
        default=0,
    )
    panel_start = layout.starts[panel_index]
    panel_end = panel_start + layout.panel_width(panel_index)
    panel_centers = [
        (source_column, float(center))
        for source_column, center in centers
        if panel_start <= source_column < panel_end
    ]
    center_index = next(
        (index for index, item in enumerate(panel_centers) if item[0] == column),
        None,
    )
    if center_index is None or len(panel_centers) < 2:
        return None
    center = panel_centers[center_index][1]
    left = (
        (panel_centers[center_index - 1][1] + center) / 2
        if center_index
        else center - (panel_centers[1][1] - center) / 2
    )
    right = (
        (center + panel_centers[center_index + 1][1]) / 2
        if center_index + 1 < len(panel_centers)
        else center + (center - panel_centers[center_index - 1][1]) / 2
    )
    return max(0, int(left)), min(image_width, int(right))


def _cell_words(
    line: list[dict[str, object]], bounds: tuple[int, int]
) -> list[dict[str, object]]:
    return sorted(
        [
            word for word in line
            if bounds[0]
            <= int(word["left"]) + int(word["width"]) / 2
            <= bounds[1]
        ],
        key=lambda word: int(word["left"]),
    )


def _observed_text(words: list[dict[str, object]]) -> str:
    return " ".join(str(word["text"]).strip() for word in words).strip()


def _row_y_bounds(
    key_words: list[dict[str, object]], image_height: int
) -> tuple[int, int]:
    top = min(int(word["top"]) for word in key_words)
    bottom = max(int(word["top"]) + int(word["height"]) for word in key_words)
    height = max(bottom - top, 1)
    return max(0, top - max(2, height // 5)), min(
        image_height, bottom + max(2, height // 5)
    )


def _cell_crop(image: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    crop = ImageOps.autocontrast(ImageOps.grayscale(image.crop(box)))
    crop = crop.resize((crop.width * 4, crop.height * 4), Image.Resampling.LANCZOS)
    crop = crop.filter(ImageFilter.UnsharpMask(radius=1, percent=100, threshold=3))
    canvas = Image.new("RGB", (max(640, crop.width), max(192, crop.height)), "white")
    canvas.paste(crop, ((canvas.width - crop.width) // 2, (canvas.height - crop.height) // 2))
    crop.close()
    return canvas


def _panel_lines(
    tsv: str, bounds: tuple[float, float]
) -> list[tuple[list[dict[str, object]], list[dict[str, object]]]]:
    candidates = []
    for line in _word_lines(tsv):
        key_words = _words_in_bounds(line, bounds)
        if key_words and any(
            character.isdigit()
            for word in key_words
            for character in str(word["text"])
        ):
            candidates.append((line, key_words))
    return sorted(
        candidates,
        key=lambda item: sum(float(word["y"]) for word in item[1]) / len(item[1]),
    )


def _one_gap_position(
    observed: list[Decimal | None], canonical: tuple[str, ...]
) -> tuple[int, int] | None:
    if len(observed) != len(canonical) - 1:
        return None
    candidates = []
    for gap in range(1, len(canonical) - 1):
        if (
            observed[gap - 1] != Decimal(canonical[gap - 1])
            or observed[gap] != Decimal(canonical[gap + 1])
        ):
            continue
        expected = canonical[:gap] + canonical[gap + 1:]
        exact = sum(
            value == Decimal(template)
            for value, template in zip(observed, expected)
        )
        candidates.append((gap, exact))
    if len(candidates) != 1:
        return None
    gap, exact = candidates[0]
    if exact / len(observed) < ONE_GAP_MINIMUM_EXACT_RATIO:
        return None
    return gap, exact


def _line_y(words: list[dict[str, object]]) -> float:
    return sum(float(word["y"]) for word in words) / len(words)


def _projection_mismatches(
    lines: list[tuple[list[dict[str, object]], list[dict[str, object]]]],
    bands: list[tuple[int, int]],
) -> list[int]:
    mismatches = []
    for position in range(max(len(lines), len(bands))):
        if position >= len(lines) or position >= len(bands):
            mismatches.append(position)
            continue
        top, bottom = bands[position]
        if not top <= _line_y(lines[position][1]) < bottom:
            mismatches.append(position)
    return mismatches


def _intervening_panel_line(
    lines: list[list[dict[str, object]]],
    bounds: tuple[float, float],
    lower_y: float,
    upper_y: float,
) -> tuple[list[dict[str, object]], list[dict[str, object]]] | None:
    candidates = []
    for line in lines:
        key_words = _words_in_bounds(line, bounds)
        if key_words and lower_y < _line_y(key_words) < upper_y:
            candidates.append((line, key_words))
    return candidates[0] if len(candidates) == 1 else None


def _aligned_panel_lines(
    tsv: str,
    bounds: tuple[float, float],
    canonical: tuple[str, ...],
    projection_bands: list[tuple[int, int]] | None = None,
    *,
    require_projection: bool = False,
) -> tuple[
    list[tuple[list[dict[str, object]], list[dict[str, object]]]] | None,
    dict[str, object],
    str | None,
]:
    numeric_lines = _panel_lines(tsv, bounds)
    observed = [
        _numeric(_observed_text(key_words))
        for _, key_words in numeric_lines
    ]
    evidence: dict[str, object] = {
        "source_lines": len(numeric_lines),
        "aligned_source_lines": len(numeric_lines),
        "alignment_method": "exact_position",
        "exact_key_matches": sum(
            value == Decimal(template)
            for value, template in zip(observed, canonical)
        ),
        "inferred_source_position": None,
    }
    if len(numeric_lines) == len(canonical):
        return numeric_lines, evidence, None
    if len(numeric_lines) != len(canonical) - 1:
        return None, evidence, "source_line_count_mismatch"

    one_gap = _one_gap_position(observed, canonical)
    if one_gap is None:
        return None, evidence, "one_gap_alignment_unavailable"
    gap, exact = one_gap
    lower_y = _line_y(numeric_lines[gap - 1][1])
    upper_y = _line_y(numeric_lines[gap][1])
    inferred = _intervening_panel_line(_word_lines(tsv), bounds, lower_y, upper_y)
    if inferred is None:
        return None, evidence, "inferred_source_line_ambiguous"

    aligned = numeric_lines[:gap] + [inferred] + numeric_lines[gap:]
    evidence.update({
        "aligned_source_lines": len(aligned),
        "alignment_method": "bracketed_one_gap",
        "exact_key_matches": exact,
        "inferred_source_position": gap,
        "inferred_template_key": canonical[gap],
        "inferred_tesseract_key": _observed_text(inferred[1]),
        "lower_anchor_key": canonical[gap - 1],
        "upper_anchor_key": canonical[gap + 1],
    })
    if require_projection and projection_bands is None:
        return None, evidence, "projection_alignment_unavailable"
    if projection_bands is not None:
        mismatches = _projection_mismatches(aligned, projection_bands)
        evidence.update({
            "projection_rows": len(projection_bands),
            "projection_mismatches": mismatches,
        })
        if mismatches:
            return None, evidence, "projection_alignment_mismatch"
    return aligned, evidence, None


def prepare(
    version_dir: Path,
    output_dir: Path,
    block_ids: set[str],
    tesseract_executable: str = "tesseract",
) -> dict:
    executable = shutil.which(tesseract_executable)
    if executable is None:
        raise FileNotFoundError(f"Tesseract executable not found: {tesseract_executable}")
    provenance_path = version_dir / "provenance.json"
    provenance = json.loads(provenance_path.read_text())
    source_pdf = version_dir.parent / "source.pdf"
    if _sha256(source_pdf) != provenance["source_sha256"]:
        raise ValueError("stored source PDF hash does not match provenance")
    tables = provenance["tables"]
    canonical = _canonical_sequence(tables)
    selected = [table for table in tables if table["block_id"] in block_ids]
    missing = block_ids - {table["block_id"] for table in selected}
    if missing:
        raise ValueError(f"source blocks not found: {', '.join(sorted(missing))}")

    output_dir.mkdir(parents=True, exist_ok=True)
    crop_dir = output_dir / "crops"
    projection_crop_dir = output_dir / "projection-crops"
    source_crop_dir = output_dir / "source-crops"
    crop_dir.mkdir(exist_ok=True)
    projection_crop_dir.mkdir(exist_ok=True)
    source_crop_dir.mkdir(exist_ok=True)
    blocks = {block["id"]: block for block in provenance["blocks"]}
    records = []
    panels_report = []
    refusals = []

    for completed, table in enumerate(selected, start=1):
        block_id = table["block_id"]
        print(f"[{completed}/{len(selected)}] {block_id}", flush=True)
        rows = gfm_rows(table.get("gfm") or "")
        panels, layout = split_repeated_panels(rows)
        if layout is None:
            refusals.append({"source_block_id": block_id, "reason": "layout_unavailable"})
            continue
        stem = block_id.strip("#/").replace("/", "_")
        source_crop = source_crop_dir / f"{stem}.png"
        _table_crop(source_pdf, table, blocks.get(block_id, {}), source_crop)
        tsv = _run_tesseract(executable, source_crop)
        aligned = dict(_aligned_tesseract_lines(rows, tsv, layout))
        centers = _column_centers(list(aligned.values()), layout)
        with Image.open(source_crop) as image:
            key_bounds = _panel_key_bounds(layout, centers, image.width)
            if centers is None or key_bounds is None:
                refusals.append({
                    "source_block_id": block_id,
                    "reason": "panel_geometry_unavailable",
                })
                continue
            panel_bounds, panel_detection, panel_detection_refusal = (
                projection_panel_bounds(image, len(layout.starts))
            )
            source_crop_hash = _sha256(source_crop)
            for panel_index, (panel, start) in enumerate(zip(panels, layout.starts)):
                recovery_start = _recovery_start(panel, canonical)
                if panel_bounds is None:
                    projection_bands = None
                    projection = None
                    projection_refusal = panel_detection_refusal
                else:
                    projection_bands, projection, projection_refusal = (
                        projection_row_bands(
                            image,
                            len(canonical),
                            panel_index=panel_index,
                            panel_count=len(layout.starts),
                            stripe_fraction=1 / layout.panel_width(panel_index),
                            panel_bounds=panel_bounds,
                        )
                    )
                if projection_bands is None or panel_bounds is None:
                    column_runs = None
                    column_projection = None
                    column_projection_refusal = projection_refusal
                else:
                    column_runs, column_projection, column_projection_refusal = (
                        projection_column_runs(
                            image,
                            projection_bands,
                            panel_bounds[panel_index],
                            layout.panel_width(panel_index),
                        )
                    )
                lines, alignment, alignment_refusal = _aligned_panel_lines(
                    tsv,
                    key_bounds[start],
                    canonical,
                    projection_bands,
                    require_projection=True,
                )
                panel_report = {
                    "source_block_id": block_id,
                    "page": int(table["page"]),
                    "panel": panel_index,
                    "source_column": start,
                    "title": panel["title"],
                    "columns": panel["columns"],
                    "structured_rows": len(panel["rows"]),
                    "canonical_rows": len(canonical),
                    "recovery_start": recovery_start,
                    "prepared_cells": 0,
                    "panel_detection": panel_detection,
                    "panel_detection_refusal": panel_detection_refusal,
                    "projection": projection,
                    "projection_refusal": projection_refusal,
                    "column_projection": column_projection,
                    "column_projection_refusal": column_projection_refusal,
                    "alignment_refusal": alignment_refusal,
                    **alignment,
                }
                panels_report.append(panel_report)
                alignment_position = alignment.get("inferred_source_position")
                if recovery_start is None and alignment_position is None:
                    continue
                if lines is None:
                    if recovery_start is not None or alignment_position is not None:
                        refusals.append({
                            **panel_report,
                            "reason": alignment_refusal,
                        })
                    continue
                positions = []
                if recovery_start is not None:
                    positions.extend(
                        (position, "control")
                        for position in _control_positions(recovery_start)
                    )
                    positions.extend(
                        (position, "recovery")
                        for position in range(recovery_start, len(canonical))
                    )
                if (
                    alignment_position is not None
                    and alignment_position not in {position for position, _ in positions}
                ):
                    positions.append((int(alignment_position), "alignment"))
                width = layout.panel_width(panel_index)
                for position, role in sorted(positions):
                    line, key_words = lines[position]
                    top, bottom = _row_y_bounds(key_words, image.height)
                    raw_row = panel["rows"][position] if position < len(panel["rows"]) else []
                    offsets = range(1) if role == "alignment" else range(width)
                    for offset in offsets:
                        source_column = start + offset
                        row_column_runs = (
                            column_runs[position]
                            if column_runs is not None and position < len(column_runs)
                            else None
                        )
                        if row_column_runs is None:
                            refusals.append({
                                "source_block_id": block_id,
                                "panel": panel_index,
                                "source_position": position,
                                "source_column": source_column,
                                "reason": "projection_column_count_mismatch",
                            })
                            continue
                        projection_x_run = row_column_runs[offset]
                        x_bounds = _column_bounds(centers, layout, source_column, image.width)
                        if x_bounds is None:
                            refusals.append({
                                "source_block_id": block_id,
                                "panel": panel_index,
                                "source_position": position,
                                "source_column": source_column,
                                "reason": "column_geometry_unavailable",
                            })
                            continue
                        projection_center = sum(projection_x_run) / 2
                        if not x_bounds[0] <= projection_center < x_bounds[1]:
                            refusals.append({
                                "source_block_id": block_id,
                                "panel": panel_index,
                                "source_position": position,
                                "source_column": source_column,
                                "reason": "projection_column_alignment_mismatch",
                            })
                            continue
                        observed = _observed_text(_cell_words(line, x_bounds))
                        if offset and typed_value(observed)[2] != "numeric":
                            continue
                        box = (x_bounds[0], top, x_bounds[1], bottom)
                        crop_path = crop_dir / (
                            f"{stem}_p{panel_index}_r{position}_c{source_column}.png"
                        )
                        crop = _cell_crop(image, box)
                        crop.save(crop_path)
                        crop.close()
                        projection_box = projection_cell_box(
                            projection_x_run,
                            projection_bands[position],
                            image.size,
                        )
                        projection_crop_path = projection_crop_dir / crop_path.name
                        projection_crop = _cell_crop(image, tuple(projection_box))
                        projection_crop.save(projection_crop_path)
                        projection_crop.close()
                        sample_id = (
                            f"{stem}:p{panel_index}:r{position}:c{source_column}"
                        )
                        records.append({
                            "id": sample_id,
                            "role": role,
                            "page": int(table["page"]),
                            "source_block_id": block_id,
                            "panel": panel_index,
                            "source_position": position,
                            "source_column": source_column,
                            "column": panel["columns"][offset],
                            "template_key": canonical[position],
                            "tesseract_value": observed,
                            "raw_value": raw_row[offset] if offset < len(raw_row) else None,
                            "alignment_position": alignment_position,
                            "source_crop": source_crop.relative_to(output_dir).as_posix(),
                            "source_crop_sha256": source_crop_hash,
                            "source_box": list(box),
                            "projection_x_run": list(projection_x_run),
                            "projection_row_band": list(projection_bands[position]),
                            "projection_box": projection_box,
                            "crop": crop_path.relative_to(output_dir).as_posix(),
                            "crop_sha256": _sha256(crop_path),
                            "projection_crop": projection_crop_path.relative_to(
                                output_dir
                            ).as_posix(),
                            "projection_crop_sha256": _sha256(projection_crop_path),
                        })
                        panel_report["prepared_cells"] += 1

    manifest = {
        "schema_version": 1,
        "method": "source_panel_row_recovery_evaluation",
        "contract": {
            "effect": "evidence overlay only; converted tables are never rewritten",
            "row_gate": "the panel's source key must match the repeated template",
            "cell_gate": "Tesseract and the pinned reader must agree at or above 0.99",
            "projection_fallback": (
                "when the OCR-box crop fails its semantic gate, retry the same pinned "
                "reader on the independently localized projection crop"
            ),
            "alignment_gate": (
                "a bracketed one-gap panel requires the inferred source key to match "
                "the repeated template under the pinned reader"
            ),
        },
        "version_dir": str(version_dir.resolve()),
        "version_provenance_sha256": _sha256(provenance_path),
        "source_sha256": provenance["source_sha256"],
        "canonical_sequence": list(canonical),
        "canonical_rows": len(canonical),
        "minimum_score": MINIMUM_SCORE,
        "pinned_reader": PINNED_READER,
        "panels": panels_report,
        "records": records,
        "refusals": refusals,
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
    projection_inputs = {
        "schema_version": 1,
        "records": [
            {
                "id": record["id"],
                "crop": record["projection_crop"],
                "crop_sha256": record["projection_crop_sha256"],
            }
            for record in records
        ],
    }
    (output_dir / "inputs-projection.json").write_text(
        json.dumps(projection_inputs, indent=2) + "\n"
    )
    return manifest


def _reader_result(
    output_dir: Path,
    prepared: dict,
    result: dict | None,
    *,
    crop_key: str = "crop",
    crop_hash_key: str = "crop_sha256",
) -> tuple[str | None, float | None, str | None]:
    source_crop = output_dir / prepared["source_crop"]
    crop = output_dir / prepared[crop_key]
    if not source_crop.is_file():
        return None, None, "source_crop_missing"
    if _sha256(source_crop) != prepared["source_crop_sha256"]:
        return None, None, "source_crop_hash_mismatch"
    if not crop.is_file():
        return None, None, "prepared_crop_missing"
    if _sha256(crop) != prepared[crop_hash_key]:
        return None, None, "prepared_crop_hash_mismatch"
    if result is None:
        return None, None, "result_missing"
    if result.get("input_sha256") != prepared[crop_hash_key]:
        return None, None, "input_hash_mismatch"
    if result.get("error"):
        return None, None, "reader_error"
    text = str(result.get("text") or "").strip()
    if not text:
        return None, None, "reader_text_missing"
    if result.get("score") is None:
        return text, None, "reader_score_missing"
    score = float(result["score"])
    if score < MINIMUM_SCORE:
        return text, score, "reader_score_below_threshold"
    return text, score, None


def _score_labels(manifest: dict, rows: list[dict], labels: dict) -> dict:
    if labels.get("schema_version") != 1:
        raise ValueError("unsupported source-row label schema_version")
    if labels.get("source_sha256") != manifest["source_sha256"]:
        raise ValueError("source-row label source hash mismatch")
    cells = {
        cell["id"]: cell
        for row in rows
        for cell in row["cells"]
    }
    outcomes = Counter()
    for label in labels.get("records", []):
        cell = cells.get(label["id"])
        if cell is None:
            outcomes["tool_refused"] += 1
        elif cell["crop_sha256"] != label["crop_sha256"]:
            raise ValueError(f"source-row label crop hash mismatch: {label['id']}")
        elif cell["candidate_value"] is None:
            outcomes["tool_refused"] += 1
        elif numeric_values_equal(cell["candidate_value"], label["expected"]):
            outcomes["agree"] += 1
        else:
            outcomes["disagree"] += 1
    return {
        "checked": len(labels.get("records", [])),
        "agree": outcomes["agree"],
        "disagree": outcomes["disagree"],
        "tool_refused": outcomes["tool_refused"],
    }


def apply(
    output_dir: Path,
    run_path: Path,
    labels_path: Path | None = None,
    projection_run_path: Path | None = None,
) -> dict:
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
    projection_run = None
    projection_results = None
    if projection_run_path is not None:
        projection_run = json.loads(projection_run_path.read_text())
        _validate_reader(projection_run.get("reader", {}))
        projection_results = {
            record["id"]: record for record in projection_run.get("records", [])
        }
        if len(projection_results) != len(projection_run.get("records", [])):
            raise ValueError("duplicate projection reader result id")

    records = []
    for prepared in manifest["records"]:
        reader_value, reader_score, refusal = _reader_result(
            output_dir, prepared, results.get(prepared["id"])
        )
        readers_agree = (
            refusal is None
            and reader_value is not None
            and numeric_values_equal(reader_value, prepared["tesseract_value"])
        )
        if projection_results is None:
            projection_reader_value = None
            projection_reader_score = None
            projection_refusal = "projection_reader_not_run"
        else:
            projection_reader_value, projection_reader_score, projection_refusal = (
                _reader_result(
                    output_dir,
                    prepared,
                    projection_results.get(prepared["id"]),
                    crop_key="projection_crop",
                    crop_hash_key="projection_crop_sha256",
                )
            )
        projection_readers_agree = (
            projection_refusal is None
            and projection_reader_value is not None
            and numeric_values_equal(
                projection_reader_value, prepared["tesseract_value"]
            )
        )
        records.append({
            **prepared,
            "reader_value": reader_value,
            "reader_score": reader_score,
            "readers_agree": readers_agree,
            "reader_refusal_reason": refusal or (
                None if readers_agree else "reader_tesseract_disagreement"
            ),
            "projection_reader_value": projection_reader_value,
            "projection_reader_score": projection_reader_score,
            "projection_readers_agree": projection_readers_agree,
            "projection_reader_refusal_reason": projection_refusal or (
                None
                if projection_readers_agree
                else "reader_tesseract_disagreement"
            ),
        })

    by_row: dict[tuple[str, int, int], list[dict]] = defaultdict(list)
    for record in records:
        by_row[
            record["source_block_id"], record["panel"], record["source_position"]
        ].append(record)
    source_key_reader = {}
    for row_id, cells in by_row.items():
        key_cell = min(cells, key=lambda cell: cell["source_column"])
        reference_confirmed = (
            key_cell["reader_refusal_reason"] in {
                None, "reader_tesseract_disagreement"
            }
            and key_cell["reader_value"] is not None
            and numeric_values_equal(key_cell["reader_value"], key_cell["template_key"])
        )
        projection_confirmed = (
            key_cell["projection_reader_refusal_reason"] in {
                None, "reader_tesseract_disagreement"
            }
            and key_cell["projection_reader_value"] is not None
            and numeric_values_equal(
                key_cell["projection_reader_value"], key_cell["template_key"]
            )
        )
        if reference_confirmed:
            source_key_reader[row_id] = "reference"
        elif projection_confirmed:
            source_key_reader[row_id] = "projection"
        else:
            source_key_reader[row_id] = None
    source_key_confirmed = {
        row_id: reader is not None for row_id, reader in source_key_reader.items()
    }
    alignment_confirmed = {
        row_id: source_key_confirmed[row_id]
        for row_id, cells in by_row.items()
        if cells[0].get("alignment_position") == row_id[2]
    }
    rows = []
    control_counts = Counter()
    for (block_id, panel, position), cells in sorted(by_row.items()):
        key_cell = min(cells, key=lambda cell: cell["source_column"])
        row_id = (block_id, panel, position)
        alignment_position = key_cell.get("alignment_position")
        alignment_ok = (
            alignment_position is None
            or alignment_confirmed.get((block_id, panel, alignment_position), False)
        )
        key_confirmed = source_key_confirmed[row_id] and alignment_ok
        key_reader = source_key_reader[row_id]
        row_cells = []
        for cell in sorted(cells, key=lambda item: item["source_column"]):
            is_key = cell["source_column"] == key_cell["source_column"]
            if not alignment_ok:
                status = (
                    "alignment_key_refused"
                    if cell["role"] == "alignment"
                    else "panel_alignment_refused"
                )
            elif is_key and key_confirmed and key_reader == "reference":
                status = "template_reader_candidate"
            elif is_key and key_confirmed:
                status = "template_projection_fallback_candidate"
            elif key_confirmed and cell["readers_agree"]:
                status = "two_reader_candidate"
            elif key_confirmed and cell["projection_readers_agree"]:
                status = "projection_fallback_candidate"
            elif not key_confirmed:
                status = "row_key_refused"
            else:
                status = "reader_refused"
            if status in {"template_reader_candidate", "two_reader_candidate"}:
                candidate = cell["reader_value"]
                accepted_reader = "reference"
            elif status in {
                "template_projection_fallback_candidate",
                "projection_fallback_candidate",
            }:
                candidate = cell["projection_reader_value"]
                accepted_reader = "projection"
            else:
                candidate = None
                accepted_reader = None
            control_outcome = None
            if cell["role"] == "control" and candidate is not None:
                control_outcome = (
                    "agree" if numeric_values_equal(candidate, cell.get("raw_value"))
                    else "disagree"
                )
                control_counts[control_outcome] += 1
            elif cell["role"] == "control":
                control_counts["tool_refused"] += 1
            row_cells.append({
                **cell,
                "status": status,
                "candidate_value": candidate,
                "accepted_reader": accepted_reader,
                "control_outcome": control_outcome,
            })
        rows.append({
            "source_block_id": block_id,
            "page": key_cell["page"],
            "panel": panel,
            "source_position": position,
            "role": key_cell["role"],
            "template_key": key_cell["template_key"],
            "source_key_confirmed": source_key_confirmed[row_id],
            "source_key_reader": source_key_reader[row_id],
            "alignment_confirmed": alignment_ok,
            "key_confirmed": key_confirmed,
            "cells": row_cells,
        })

    with (output_dir / "rows.jsonl").open("w") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    status_counts = Counter(
        cell["status"] for row in rows for cell in row["cells"]
    )
    role_rows = Counter((row["role"], row["key_confirmed"]) for row in rows)
    alignment_checks = []
    for row in rows:
        if row["role"] != "alignment":
            continue
        cell = row["cells"][0]
        alignment_checks.append({
            "source_block_id": row["source_block_id"],
            "page": row["page"],
            "panel": row["panel"],
            "source_position": row["source_position"],
            "template_key": row["template_key"],
            "tesseract_value": cell["tesseract_value"],
            "reader_value": cell["reader_value"],
            "reader_score": cell["reader_score"],
            "projection_reader_value": cell["projection_reader_value"],
            "projection_reader_score": cell["projection_reader_score"],
            "source_key_reader": row["source_key_reader"],
            "status": cell["status"],
        })
    report = {
        "schema_version": 1,
        "method": manifest["method"],
        "contract": manifest["contract"],
        "source_sha256": manifest["source_sha256"],
        "version_provenance_sha256": manifest["version_provenance_sha256"],
        "manifest_sha256": _sha256(manifest_path),
        "run_sha256": _sha256(run_path),
        "reader": run["reader"],
        "projection_run_sha256": (
            _sha256(projection_run_path) if projection_run_path is not None else None
        ),
        "projection_reader": (
            projection_run["reader"] if projection_run is not None else None
        ),
        "prepared_cells": len(records),
        "rows": len(rows),
        "recovery_rows": sum(row["role"] == "recovery" for row in rows),
        "recovery_rows_key_confirmed": role_rows["recovery", True],
        "control_rows": sum(row["role"] == "control" for row in rows),
        "control_rows_key_confirmed": role_rows["control", True],
        "alignment_rows": sum(row["role"] == "alignment" for row in rows),
        "alignment_rows_key_confirmed": role_rows["alignment", True],
        "alignment_checks": alignment_checks,
        "cell_statuses": dict(status_counts),
        "accepted_readers": dict(
            Counter(
                cell["accepted_reader"]
                for row in rows
                for cell in row["cells"]
                if cell["accepted_reader"] is not None
            )
        ),
        "control": {
            "agree": control_counts["agree"],
            "disagree": control_counts["disagree"],
            "tool_refused": control_counts["tool_refused"],
        },
        "panels": manifest["panels"],
        "preparation_refusals": manifest["refusals"],
        "rows_file": "rows.jsonl",
    }
    if labels_path is not None:
        report["source_labels"] = _score_labels(
            manifest, rows, json.loads(labels_path.read_text())
        )
        report["source_labels_sha256"] = _sha256(labels_path)
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("version_dir", type=Path)
    prepare_parser.add_argument("output_dir", type=Path)
    prepare_parser.add_argument("--block-id", action="append", required=True)
    prepare_parser.add_argument("--tesseract", default="tesseract")
    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("output_dir", type=Path)
    apply_parser.add_argument("--run", type=Path, required=True)
    apply_parser.add_argument("--projection-run", type=Path)
    apply_parser.add_argument("--labels", type=Path)
    args = parser.parse_args()

    if args.command == "prepare":
        manifest = prepare(
            args.version_dir, args.output_dir, set(args.block_id), args.tesseract
        )
        print(
            f"source rows: {len(manifest['records'])} cell crops from "
            f"{len(manifest['panels'])} panels"
        )
        print(args.output_dir / "inputs.json")
        print(args.output_dir / "inputs-projection.json")
    else:
        report = apply(
            args.output_dir,
            args.run,
            args.labels,
            args.projection_run,
        )
        print(
            f"source rows: {report['recovery_rows_key_confirmed']}/"
            f"{report['recovery_rows']} recovery keys confirmed; "
            f"controls {report['control']['agree']} agree, "
            f"{report['control']['disagree']} disagree, "
            f"{report['control']['tool_refused']} refused"
        )
        if report["projection_reader"] is not None:
            print(
                f"accepted readers: {report['accepted_readers'].get('reference', 0)} "
                f"reference, {report['accepted_readers'].get('projection', 0)} "
                "projection fallback"
            )
        print(args.output_dir / "report.json")


if __name__ == "__main__":
    main()
