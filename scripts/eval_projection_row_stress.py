"""Stress the token-free table-row locator with deterministic image transforms.

Baseline Tesseract row centers are carried through known geometric transforms as an
independent reference. The report separates raw locator errors from gate refusals.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from pdf2md.line_reader import _run_tesseract, _sha256, _table_crop
from pdf2md.row_locator import (
    projection_column_runs,
    projection_panel_bounds,
    projection_row_bands,
)


ROOT = Path(__file__).parent.parent
DEFAULT_CORPUS = ROOT / "tests" / "projection_row_stress_corpus.json"
DEFAULT_OUTPUT = ROOT / "out" / "reviews" / "projection-row-stress-v1"

spec = importlib.util.spec_from_file_location(
    "eval_source_row_recovery", ROOT / "scripts" / "eval_source_row_recovery.py"
)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


def _row_centers(
    lines: list[tuple[list[dict[str, object]], list[dict[str, object]]]],
) -> list[tuple[float, float]]:
    return [
        (
            sum(float(word["x"]) for word in key_words) / len(key_words),
            recovery._line_y(key_words),
        )
        for _, key_words in lines
    ]


def _rotate_point(
    point: tuple[float, float], size: tuple[int, int], angle: float
) -> tuple[float, float]:
    x, y = point
    center_x, center_y = size[0] / 2, size[1] / 2
    radians = math.radians(angle)
    return (
        center_x
        + math.cos(radians) * (x - center_x)
        + math.sin(radians) * (y - center_y),
        center_y
        - math.sin(radians) * (x - center_x)
        + math.cos(radians) * (y - center_y),
    )


def _translated(image: Image.Image, dx: int, dy: int) -> Image.Image:
    translated = Image.new("RGB", image.size, "white")
    translated.paste(image, (dx, dy))
    return translated


def _salt_pepper(
    image: Image.Image, rate: float, seed: int
) -> Image.Image:
    pixels = np.asarray(image.convert("RGB")).copy()
    random = np.random.default_rng(seed)
    count = round(pixels.shape[0] * pixels.shape[1] * rate)
    flat = random.choice(pixels.shape[0] * pixels.shape[1], count, replace=False)
    y, x = np.divmod(flat, pixels.shape[1])
    values = random.integers(0, 2, size=count, dtype=np.uint8)[:, None] * 255
    pixels[y, x] = values
    return Image.fromarray(pixels)


def _bowed(
    image: Image.Image,
    amplitude: float,
    panel_index: int,
    panel_count: int,
) -> tuple[Image.Image, list[int]]:
    pixels = np.asarray(image.convert("RGB"))
    warped = np.full_like(pixels, 255)
    left = round(image.width * panel_index / panel_count)
    right = round(image.width * (panel_index + 1) / panel_count)
    center = (left + right) / 2
    half_width = (right - left) / 2
    offsets = []
    for x in range(image.width):
        local = min(1.0, abs((x - center) / half_width))
        offset = round(amplitude * (1 - local * local)) if left <= x < right else 0
        offsets.append(offset)
        warped[offset:, x] = pixels[: image.height - offset, x]
    return Image.fromarray(warped), offsets


def _unequal_panels(
    image: Image.Image, left_fraction: float
) -> Image.Image:
    old_split = image.width // 2
    new_split = round(image.width * left_fraction)
    left = image.crop((0, 0, old_split, image.height)).resize(
        (new_split, image.height), Image.Resampling.BICUBIC
    )
    right = image.crop((old_split, 0, image.width, image.height)).resize(
        (image.width - new_split, image.height), Image.Resampling.BICUBIC
    )
    combined = Image.new("RGB", image.size, "white")
    combined.paste(left, (0, 0))
    combined.paste(right, (new_split, 0))
    left.close()
    right.close()
    return combined


def _apply_operations(
    source: Image.Image,
    centers: list[tuple[float, float]],
    operations: list[dict],
    panel_index: int,
    panel_count: int,
) -> tuple[Image.Image, list[tuple[float, float]], list[dict]]:
    image = source.convert("RGB")
    transformed = list(centers)
    applied = []
    for operation in operations:
        kind = operation["type"]
        recorded = dict(operation)
        if kind == "rotate":
            angle = float(operation["angle"])
            size = image.size
            next_image = image.rotate(
                angle,
                resample=Image.Resampling.BICUBIC,
                expand=False,
                fillcolor="white",
            )
            transformed = [_rotate_point(point, size, angle) for point in transformed]
        elif kind == "deskew":
            from pdf2md.scan_deskew import deskew_image

            next_image, angle = deskew_image(image)
            recorded["applied_angle"] = angle
            transformed = [
                _rotate_point(point, image.size, angle) for point in transformed
            ]
        elif kind == "translate":
            dx, dy = int(operation["dx"]), int(operation.get("dy", 0))
            next_image = _translated(image, dx, dy)
            transformed = [(x + dx, y + dy) for x, y in transformed]
        elif kind == "crop":
            left = int(operation.get("left", 0))
            top = int(operation.get("top", 0))
            right = image.width - int(operation.get("right", 0))
            bottom = image.height - int(operation.get("bottom", 0))
            next_image = image.crop((left, top, right, bottom))
            transformed = [(x - left, y - top) for x, y in transformed]
        elif kind == "salt_pepper":
            next_image = _salt_pepper(
                image, float(operation["rate"]), int(operation["seed"])
            )
        elif kind == "bow":
            next_image, offsets = _bowed(
                image, float(operation["amplitude"]), panel_index, panel_count
            )
            transformed = [
                (x, y + offsets[min(max(round(x), 0), len(offsets) - 1)])
                for x, y in transformed
            ]
        elif kind == "unequal_panels":
            if panel_count != 2:
                raise ValueError("unequal_panels requires exactly two panels")
            left_fraction = float(operation["left_fraction"])
            old_split = image.width // 2
            new_split = round(image.width * left_fraction)
            left_scale = new_split / old_split
            right_scale = (image.width - new_split) / (image.width - old_split)
            transformed = [
                (
                    x * left_scale
                    if x < old_split
                    else new_split + (x - old_split) * right_scale,
                    y,
                )
                for x, y in transformed
            ]
            next_image = _unequal_panels(image, left_fraction)
        elif kind == "blur":
            next_image = image.filter(
                ImageFilter.GaussianBlur(float(operation["radius"]))
            )
        elif kind in {"horizontal_rule", "rectangle"}:
            next_image = image.copy()
            drawing = ImageDraw.Draw(next_image)
            if kind == "horizontal_rule":
                y = int(operation["y"])
                drawing.line(
                    (0, y, next_image.width, y),
                    fill="black",
                    width=int(operation.get("width", 1)),
                )
            else:
                drawing.rectangle(tuple(operation["box"]), fill="black")
        else:
            image.close()
            raise ValueError(f"unsupported projection stress operation: {kind}")
        image.close()
        image = next_image
        applied.append(recorded)
    return image, transformed, applied


def _reference_lines(panel: dict, tsv: str, provenance: dict) -> list:
    bounds = tuple(panel["key_bounds"])
    if panel["reference_method"] == "numeric_lines":
        lines = recovery._panel_lines(tsv, bounds)
    elif panel["reference_method"] == "canonical_one_gap":
        canonical = recovery._canonical_sequence(provenance["tables"])
        lines, _, refusal = recovery._aligned_panel_lines(tsv, bounds, canonical)
        if lines is None:
            raise ValueError(f"baseline row alignment refused: {panel['id']}: {refusal}")
    else:
        raise ValueError(f"unsupported reference method: {panel['reference_method']}")
    if len(lines) != int(panel["expected_rows"]):
        raise ValueError(
            f"baseline row count mismatch: {panel['id']} "
            f"({len(lines)} != {panel['expected_rows']})"
        )
    return lines


def _mismatch_positions(
    centers: list[tuple[float, float]], bands: list[tuple[int, int]]
) -> list[int]:
    reference_lines = [([], [{"y": y}]) for _, y in centers]
    return recovery._projection_mismatches(reference_lines, bands)


def _case_outcomes(
    centers: list[tuple[float, float]],
    bands: list[tuple[int, int]] | None,
) -> tuple[str, str, list[int]]:
    if bands is None:
        return "tool_refused", "tool_refused", list(range(len(centers)))
    mismatches = _mismatch_positions(centers, bands)
    if mismatches:
        return "disagree", "tool_refused", mismatches
    return "agree", "agree", []


def _counts(records: list[dict], field: str) -> dict[str, int]:
    counts = Counter(record[field] for record in records)
    return {
        outcome: counts[outcome]
        for outcome in ("agree", "disagree", "tool_refused")
    }


def evaluate(root: Path, corpus: dict, output_dir: Path, tesseract: str) -> dict:
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported projection-row stress schema_version")
    executable = shutil.which(tesseract)
    if executable is None:
        raise FileNotFoundError(f"Tesseract executable not found: {tesseract}")
    output_dir.mkdir(parents=True, exist_ok=True)
    crop_dir = output_dir / "source-crops"
    case_dir = output_dir / "cases"
    tsv_dir = output_dir / "tesseract"
    crop_dir.mkdir(exist_ok=True)
    case_dir.mkdir(exist_ok=True)
    tsv_dir.mkdir(exist_ok=True)

    panels = {}
    panel_reports = []
    for index, panel in enumerate(corpus["panels"], start=1):
        print(f"[panel {index}/{len(corpus['panels'])}] {panel['id']}", flush=True)
        version_dir = root / panel["version_dir"]
        provenance_path = version_dir / "provenance.json"
        if _sha256(provenance_path) != panel["provenance_sha256"]:
            raise ValueError(f"provenance hash mismatch: {panel['id']}")
        provenance = json.loads(provenance_path.read_text())
        source_pdf = version_dir.parent / "source.pdf"
        if _sha256(source_pdf) != panel["source_sha256"]:
            raise ValueError(f"source PDF hash mismatch: {panel['id']}")
        table = next(
            (
                table for table in provenance["tables"]
                if table["block_id"] == panel["block_id"]
            ),
            None,
        )
        if table is None or int(table["page"]) != int(panel["page"]):
            raise ValueError(f"source table mismatch: {panel['id']}")
        blocks = {block["id"]: block for block in provenance["blocks"]}
        crop_path = crop_dir / f"{panel['id']}.png"
        _table_crop(source_pdf, table, blocks[panel["block_id"]], crop_path)
        if _sha256(crop_path) != panel["source_crop_sha256"]:
            raise ValueError(f"source crop hash mismatch: {panel['id']}")
        tsv = _run_tesseract(executable, crop_path)
        tsv_path = tsv_dir / f"{panel['id']}.tsv"
        tsv_path.write_text(tsv)
        lines = _reference_lines(panel, tsv, provenance)
        panels[panel["id"]] = {
            "config": panel,
            "crop_path": crop_path,
            "centers": _row_centers(lines),
        }
        panel_reports.append({
            "id": panel["id"],
            "source_sha256": panel["source_sha256"],
            "page": panel["page"],
            "block_id": panel["block_id"],
            "rows": len(lines),
            "source_crop": crop_path.relative_to(output_dir).as_posix(),
            "source_crop_sha256": _sha256(crop_path),
            "tesseract_tsv": tsv_path.relative_to(output_dir).as_posix(),
            "tesseract_tsv_sha256": _sha256(tsv_path),
        })

    records = []
    for index, case in enumerate(corpus["cases"], start=1):
        print(f"[case {index}/{len(corpus['cases'])}] {case['id']}", flush=True)
        source = panels[case["panel_id"]]
        panel = source["config"]
        with Image.open(source["crop_path"]) as image:
            transformed, centers, applied = _apply_operations(
                image,
                source["centers"],
                case["operations"],
                int(panel["projection_panel_index"]),
                int(panel["projection_panel_count"]),
            )
        case_path = case_dir / f"{case['id']}.png"
        transformed.save(case_path)
        bands, evidence, refusal = projection_row_bands(
            transformed,
            int(panel["expected_rows"]),
            panel_index=int(panel["projection_panel_index"]),
            panel_count=int(panel["projection_panel_count"]),
            stripe_fraction=float(panel["projection_stripe_fraction"]),
        )
        detected_bounds, panel_detection, panel_refusal = projection_panel_bounds(
            transformed, int(panel["projection_panel_count"])
        )
        if detected_bounds is None:
            detected_bands = None
            detected_evidence = None
            detected_refusal = panel_refusal
        else:
            detected_bands, detected_evidence, detected_refusal = projection_row_bands(
                transformed,
                int(panel["expected_rows"]),
                panel_index=int(panel["projection_panel_index"]),
                panel_count=int(panel["projection_panel_count"]),
                stripe_fraction=float(panel["projection_stripe_fraction"]),
                panel_bounds=detected_bounds,
            )
        if detected_bounds is None or detected_bands is None:
            column_rows = None
            column_evidence = None
            column_refusal = detected_refusal
        else:
            column_rows, column_evidence, column_refusal = projection_column_runs(
                transformed,
                detected_bands,
                detected_bounds[int(panel["projection_panel_index"])],
                int(panel["expected_columns"]),
            )
        transformed.close()
        raw_outcome, gate_outcome, mismatches = _case_outcomes(centers, bands)
        detected_raw, detected_gate, detected_mismatches = _case_outcomes(
            centers, detected_bands
        )
        if detected_gate != "agree" or column_rows is None:
            column_gate = "tool_refused"
            column_rows_checked = 0
            column_rows_exact = 0
        else:
            column_rows_checked = len(column_rows)
            column_rows_exact = sum(row is not None for row in column_rows)
            column_gate = (
                "agree"
                if column_rows_exact == column_rows_checked
                else "tool_refused"
            )
        records.append({
            "id": case["id"],
            "family": case["family"],
            "panel_id": case["panel_id"],
            "operations": applied,
            "expected_raw_outcome": case["expected_raw_outcome"],
            "raw_outcome": raw_outcome,
            "expected_gate_outcome": case["expected_gate_outcome"],
            "gate_outcome": gate_outcome,
            "mismatch_positions": mismatches,
            "projection_refusal": refusal,
            "projection": evidence,
            "expected_detected_raw_outcome": case.get(
                "expected_detected_raw_outcome", case["expected_raw_outcome"]
            ),
            "detected_raw_outcome": detected_raw,
            "expected_detected_gate_outcome": case.get(
                "expected_detected_gate_outcome", case["expected_gate_outcome"]
            ),
            "detected_gate_outcome": detected_gate,
            "detected_mismatch_positions": detected_mismatches,
            "panel_detection_refusal": panel_refusal,
            "panel_detection": panel_detection,
            "detected_projection_refusal": detected_refusal,
            "detected_projection": detected_evidence,
            "column_gate_outcome": column_gate,
            "column_rows_checked": column_rows_checked,
            "column_rows_exact": column_rows_exact,
            "column_rows_refused": column_rows_checked - column_rows_exact,
            "column_projection_refusal": column_refusal,
            "column_projection": column_evidence,
            "image": case_path.relative_to(output_dir).as_posix(),
            "image_sha256": _sha256(case_path),
        })

    raw = _counts(records, "raw_outcome")
    gate = _counts(records, "gate_outcome")
    detected_raw = _counts(records, "detected_raw_outcome")
    detected_gate = _counts(records, "detected_gate_outcome")
    column_gate = _counts(records, "column_gate_outcome")
    families = {}
    for family in sorted({record["family"] for record in records}):
        family_records = [record for record in records if record["family"] == family]
        families[family] = {
            "cases": len(family_records),
            "raw": _counts(family_records, "raw_outcome"),
            "gate": _counts(family_records, "gate_outcome"),
            "detected_raw": _counts(family_records, "detected_raw_outcome"),
            "detected_gate": _counts(family_records, "detected_gate_outcome"),
            "column_gate": _counts(family_records, "column_gate_outcome"),
        }
    expectation_mismatches = [
        record["id"]
        for record in records
        if record["raw_outcome"] != record["expected_raw_outcome"]
        or record["gate_outcome"] != record["expected_gate_outcome"]
        or record["detected_raw_outcome"]
        != record["expected_detected_raw_outcome"]
        or record["detected_gate_outcome"]
        != record["expected_detected_gate_outcome"]
    ]
    report = {
        "schema_version": 1,
        "method": "controlled_projection_row_locator_stress",
        "contract": {
            "reference": (
                "source-checked Tesseract row centers carried through each known "
                "geometric transform; projection receives pixels only"
            ),
            "raw_outcomes": ["agree", "disagree", "tool_refused"],
            "gate": (
                "a raw disagreement or locator refusal becomes a row-alignment "
                "refusal; only complete center-to-band agreement is accepted"
            ),
        },
        "corpus_sha256": corpus.get("_corpus_sha256"),
        "tesseract": {
            "executable": executable,
            "version": subprocess.run(
                [executable, "--version"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()[0],
        },
        "panels": len(panels),
        "cases_checked": len(records),
        "raw": raw,
        "gate": gate,
        "detected_raw": detected_raw,
        "detected_gate": detected_gate,
        "column_gate": column_gate,
        "column_rows_checked": sum(
            record["column_rows_checked"] for record in records
        ),
        "column_rows_exact": sum(record["column_rows_exact"] for record in records),
        "column_rows_refused": sum(
            record["column_rows_refused"] for record in records
        ),
        "families": families,
        "accepted_wrong_mappings": gate["disagree"],
        "detected_accepted_wrong_mappings": detected_gate["disagree"],
        "expectation_mismatches": expectation_mismatches,
        "panels_report": panel_reports,
        "cases": records,
    }
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--tesseract", default="tesseract")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    corpus = json.loads(args.corpus.read_text())
    corpus["_corpus_sha256"] = _sha256(args.corpus)
    report = evaluate(args.root, corpus, args.output, args.tesseract)
    print(
        f"projection stress: {report['cases_checked']} cases; "
        f"raw {report['raw']['agree']} agree, {report['raw']['disagree']} disagree, "
        f"{report['raw']['tool_refused']} refused; gate "
        f"{report['gate']['agree']} agree, {report['gate']['disagree']} disagree, "
        f"{report['gate']['tool_refused']} refused; detected bounds "
        f"{report['detected_raw']['agree']} agree, "
        f"{report['detected_raw']['disagree']} disagree, "
        f"{report['detected_raw']['tool_refused']} refused; detected gate "
        f"{report['detected_gate']['agree']} agree, "
        f"{report['detected_gate']['disagree']} disagree, "
        f"{report['detected_gate']['tool_refused']} refused; column gate "
        f"{report['column_gate']['agree']} agree, "
        f"{report['column_gate']['disagree']} disagree, "
        f"{report['column_gate']['tool_refused']} refused; "
        f"{report['column_rows_exact']}/{report['column_rows_checked']} exact rows"
    )
    if args.check:
        expected = corpus.get("expected")
        actual = {
            "cases_checked": report["cases_checked"],
            "raw": report["raw"],
            "gate": report["gate"],
            "detected_raw": report["detected_raw"],
            "detected_gate": report["detected_gate"],
            "column_gate": report["column_gate"],
            "column_rows_checked": report["column_rows_checked"],
            "column_rows_exact": report["column_rows_exact"],
            "column_rows_refused": report["column_rows_refused"],
            "accepted_wrong_mappings": report["accepted_wrong_mappings"],
            "detected_accepted_wrong_mappings": report[
                "detected_accepted_wrong_mappings"
            ],
            "expectation_mismatches": report["expectation_mismatches"],
        }
        if expected is None or actual != expected:
            print(json.dumps({"expected": expected, "actual": actual}, indent=2))
            raise SystemExit(1)


if __name__ == "__main__":
    main()
