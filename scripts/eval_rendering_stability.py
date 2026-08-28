"""Measure PP-OCRv6 stability across render and preprocessing choices."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from pdf2md.line_reader import MINIMUM_SCORE, _sha256, _validate_reader
from pdf2md.render import CropRenderer
from pdf2md.scan_deskew import deskew_image
from pdf2md.schema import BBox


ROOT = Path(__file__).parent.parent
DEFAULT_LABELS = ROOT / "tests" / "heldout_data_cell_labels.json"
DEFAULT_SOURCE_CORPUS = ROOT / "tests" / "source_row_alignment_corpus.json"
DEFAULT_REFERENCE = ROOT / "out" / "reviews" / "heldout-data-reader-v1"
DEFAULT_OUTPUT = ROOT / "out" / "reviews" / "rendering-stability-v1"
DEFAULT_CORPUS = ROOT / "tests" / "rendering_stability_corpus.json"
DPIS = (300, 450, 600)
PIXEL_MODES = ("grayscale", "adaptive")
DESKEW_MODES = ("original", "deskewed")
PADDING_MODES = ("tight", "padded")
BASELINE_VARIANT = "dpi300-grayscale-original-padded"

HELDOUT_SPEC = importlib.util.spec_from_file_location(
    "eval_heldout_data_reader", ROOT / "scripts" / "eval_heldout_data_reader.py"
)
heldout = importlib.util.module_from_spec(HELDOUT_SPEC)
HELDOUT_SPEC.loader.exec_module(heldout)


def _variant_id(dpi: int, pixels: str, deskew: str, padding: str) -> str:
    return f"dpi{dpi}-{pixels}-{deskew}-{padding}"


def _adaptive_binary(image: Image.Image, dpi: int) -> Image.Image:
    gray = ImageOps.autocontrast(image.convert("L"))
    radius = max(3, round(dpi / 75))
    local_mean = np.asarray(gray.filter(ImageFilter.BoxBlur(radius)), dtype=np.int16)
    values = np.asarray(gray, dtype=np.int16)
    return Image.fromarray(np.where(values < local_mean - 12, 0, 255).astype(np.uint8))


def _scaled_box(
    box: list[int], base_size: tuple[int, int], rendered_size: tuple[int, int]
) -> tuple[int, int, int, int]:
    x_scale = rendered_size[0] / base_size[0]
    y_scale = rendered_size[1] / base_size[1]
    left, top, right, bottom = (
        round(box[0] * x_scale),
        round(box[1] * y_scale),
        round(box[2] * x_scale),
        round(box[3] * y_scale),
    )
    return (
        max(0, min(left, rendered_size[0] - 1)),
        max(0, min(top, rendered_size[1] - 1)),
        max(1, min(right, rendered_size[0])),
        max(1, min(bottom, rendered_size[1])),
    )


def _tight_box(record: dict) -> list[int]:
    return [
        int(record["projection_x_run"][0]),
        int(record["projection_row_band"][0]),
        int(record["projection_x_run"][1]),
        int(record["projection_row_band"][1]),
    ]


def _panel_sources(labels: dict, source_corpus: dict) -> dict[str, dict]:
    known = {panel["id"]: panel for panel in source_corpus["panels"]}
    panels = {}
    for labelled in labels["panels"]:
        panel = known.get(labelled["id"], labelled.get("source_panel"))
        if panel is None:
            raise ValueError(f"source panel unavailable: {labelled['id']}")
        panels[labelled["id"]] = panel
    return panels


def _render_panel(panel: dict, dpi: int, path: Path) -> None:
    provenance_path = ROOT / panel["version_dir"] / "provenance.json"
    if _sha256(provenance_path) != panel["provenance_sha256"]:
        raise ValueError(f"provenance hash mismatch: {panel['id']}")
    provenance = json.loads(provenance_path.read_text())
    table = next(
        item for item in provenance["tables"] if item["block_id"] == panel["block_id"]
    )
    block = next(
        item for item in provenance["blocks"] if item["id"] == panel["block_id"]
    )
    bbox = table.get("bbox") or block.get("bbox")
    if bbox is None:
        raise ValueError(f"table bbox missing: {panel['id']}")
    source_path = ROOT / panel["source"]
    if _sha256(source_path) != panel["source_sha256"]:
        raise ValueError(f"source PDF hash mismatch: {panel['id']}")
    with CropRenderer(source_path, dpi=dpi, padding_pts=6.0) as renderer:
        renderer.crop(int(table["page"]), BBox(**bbox), path, dpi=dpi)


def prepare(
    labels_path: Path,
    source_corpus_path: Path,
    reference_dir: Path,
    output_dir: Path,
) -> dict:
    labels = json.loads(labels_path.read_text())
    source_corpus = json.loads(source_corpus_path.read_text())
    reference_path = reference_dir / "manifest.json"
    reference = json.loads(reference_path.read_text())
    if reference["labels_sha256"] != _sha256(labels_path):
        raise ValueError("held-out label hash mismatch")
    if reference["source_corpus_sha256"] != _sha256(source_corpus_path):
        raise ValueError("held-out source corpus hash mismatch")

    panels = _panel_sources(labels, source_corpus)
    records_by_panel: dict[str, list[dict]] = {}
    for record in reference["records"]:
        records_by_panel.setdefault(record["panel_id"], []).append(record)

    render_dir = output_dir / "renders"
    crop_dir = output_dir / "crops"
    render_dir.mkdir(parents=True, exist_ok=True)
    crop_dir.mkdir(parents=True, exist_ok=True)
    prepared = []
    render_records = []
    variants = list(itertools.product(DPIS, PIXEL_MODES, DESKEW_MODES, PADDING_MODES))
    total = len(reference["records"]) * len(variants)
    completed = 0
    for panel_index, (panel_id, base_records) in enumerate(
        sorted(records_by_panel.items()), start=1
    ):
        print(f"[{panel_index}/{len(records_by_panel)}] {panel_id}", flush=True)
        source_crop_path = Path(base_records[0]["source_crop"])
        expected_source_hash = {record["source_crop_sha256"] for record in base_records}
        if len(expected_source_hash) != 1 or _sha256(source_crop_path) != expected_source_hash.pop():
            raise ValueError(f"reference source crop hash mismatch: {panel_id}")
        with Image.open(source_crop_path) as base_image:
            base_size = base_image.size
        for dpi in DPIS:
            render_path = render_dir / f"{panel_id}-dpi{dpi}.png"
            _render_panel(panels[panel_id], dpi, render_path)
            with Image.open(render_path) as rendered_source:
                original = rendered_source.convert("RGB")
            corrected, angle = deskew_image(original)
            images = {"original": original, "deskewed": corrected}
            render_records.append({
                "panel_id": panel_id,
                "dpi": dpi,
                "path": render_path.relative_to(output_dir).as_posix(),
                "sha256": _sha256(render_path),
                "size": list(original.size),
                "deskew_degrees": angle,
                "matches_reference_300dpi": (
                    dpi == 300 and _sha256(render_path) == base_records[0]["source_crop_sha256"]
                ),
            })
            for record in base_records:
                boxes = {
                    "tight": _tight_box(record),
                    "padded": record["projection_box"],
                }
                for pixels, deskew, padding in itertools.product(
                    PIXEL_MODES, DESKEW_MODES, PADDING_MODES
                ):
                    variant = _variant_id(dpi, pixels, deskew, padding)
                    box = _scaled_box(boxes[padding], base_size, original.size)
                    crop = images[deskew].crop(box)
                    processed = (
                        ImageOps.autocontrast(crop.convert("L"))
                        if pixels == "grayscale" else _adaptive_binary(crop, dpi)
                    )
                    crop_path = crop_dir / f"{record['id'].replace(':', '_')}-{variant}.png"
                    processed.save(crop_path)
                    processed.close()
                    crop.close()
                    prepared.append({
                        "id": f"{record['id']}|{variant}",
                        "base_id": record["id"],
                        "panel_id": panel_id,
                        "class": record["class"],
                        "expected": record["expected"],
                        "expected_kind": record.get("expected_kind", "numeric"),
                        "primary": record["primary"],
                        "variant": variant,
                        "dpi": dpi,
                        "pixels": pixels,
                        "deskew": deskew,
                        "padding": padding,
                        "box": list(box),
                        "crop": crop_path.relative_to(output_dir).as_posix(),
                        "crop_sha256": _sha256(crop_path),
                    })
                    completed += 1
                    if completed % 100 == 0 or completed == total:
                        print(f"  prepared {completed}/{total}", flush=True)
            original.close()
            corrected.close()

    manifest = {
        "schema_version": 1,
        "method": "factorial_rendering_stability",
        "contract": {
            "design": {
                "dpi": list(DPIS),
                "pixels": list(PIXEL_MODES),
                "deskew": list(DESKEW_MODES),
                "padding": list(PADDING_MODES),
            },
            "reader": "one pinned PP-OCRv6_medium_rec model across every condition",
            "labels": "source-checked before this rendering experiment",
            "independence": "shared source and recognition model; agreement is stability evidence only",
            "baseline_variant": BASELINE_VARIANT,
        },
        "labels_sha256": _sha256(labels_path),
        "source_corpus_sha256": _sha256(source_corpus_path),
        "reference_manifest_sha256": _sha256(reference_path),
        "cells": len(reference["records"]),
        "variants": len(variants),
        "crops": len(prepared),
        "renders": render_records,
        "records": prepared,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    inputs = {
        "schema_version": 1,
        "records": [
            {key: record[key] for key in ("id", "crop", "crop_sha256")}
            for record in prepared
        ],
    }
    (output_dir / "inputs.json").write_text(json.dumps(inputs, indent=2) + "\n")
    return manifest


def _observation(result: dict | None) -> tuple[str | None, str | None]:
    if result is None or result.get("error"):
        return None, "reader_error"
    text = str(result.get("text") or "").strip()
    if not text:
        return None, "reader_text_missing"
    if result.get("score") is None or float(result["score"]) < MINIMUM_SCORE:
        return text, "reader_score_below_threshold"
    return text, None


def _summary(counter: Counter) -> dict:
    return {
        "checked": sum(counter.values()),
        "agree": counter["agree"],
        "disagree": counter["disagree"],
        "tool_refused": counter["tool_refused"],
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 8) if denominator else None


def _prediction_summary(records: list[dict], results: dict[str, dict]) -> dict:
    by_cell: dict[str, list[dict]] = {}
    for record in records:
        by_cell.setdefault(record["base_id"], []).append(record)
    counts = Counter()
    primary_errors = Counter()
    details = []
    for base_id, variants in sorted(by_cell.items()):
        baseline = next(record for record in variants if record["variant"] == BASELINE_VARIANT)
        comparison = [record for record in variants if record["variant"] != BASELINE_VARIANT]
        signatures = set()
        for record in comparison:
            value, refusal = _observation(results.get(record["id"]))
            signatures.add((value, refusal))
        unstable = len(signatures) > 1
        baseline_value, baseline_refusal = _observation(results.get(baseline["id"]))
        baseline_outcome = heldout._outcome(
            baseline_value,
            baseline_refusal,
            baseline["expected"],
            baseline["expected_kind"],
        )
        primary_outcome = heldout._outcome(
            baseline["primary"], None, baseline["expected"], baseline["expected_kind"]
        )
        group = "unstable" if unstable else "stable"
        counts[f"{group}_cells"] += 1
        counts[f"{group}_baseline_adverse"] += baseline_outcome != "agree"
        primary_errors[f"{group}_primary_errors"] += primary_outcome != "agree"
        details.append({
            "base_id": base_id,
            "unstable_off_baseline": unstable,
            "off_baseline_signatures": len(signatures),
            "baseline_outcome": baseline_outcome,
            "primary_outcome": primary_outcome,
        })
    unstable_cells = counts["unstable_cells"]
    stable_cells = counts["stable_cells"]
    unstable_adverse = counts["unstable_baseline_adverse"]
    stable_adverse = counts["stable_baseline_adverse"]
    adverse_cells = unstable_adverse + stable_adverse
    nonadverse_cells = unstable_cells + stable_cells - adverse_cells
    return {
        "baseline_variant": BASELINE_VARIANT,
        "instability_uses_baseline": False,
        "unstable_cells": unstable_cells,
        "unstable_baseline_adverse": unstable_adverse,
        "stable_cells": stable_cells,
        "stable_baseline_adverse": stable_adverse,
        "baseline_adverse_sensitivity": _rate(unstable_adverse, adverse_cells),
        "baseline_adverse_specificity": _rate(
            stable_cells - stable_adverse, nonadverse_cells
        ),
        "baseline_adverse_positive_predictive_value": _rate(
            unstable_adverse, unstable_cells
        ),
        "baseline_adverse_negative_predictive_value": _rate(
            stable_cells - stable_adverse, stable_cells
        ),
        "unstable_primary_errors": primary_errors["unstable_primary_errors"],
        "stable_primary_errors": primary_errors["stable_primary_errors"],
        "primary_error_prediction_available": (
            primary_errors["unstable_primary_errors"] + primary_errors["stable_primary_errors"] > 0
        ),
        "cells": details,
    }


def compare(output_dir: Path, run_path: Path) -> dict:
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    run = json.loads(run_path.read_text())
    _validate_reader(run.get("reader", {}))
    records = {record["id"]: record for record in manifest["records"]}
    results = {record["id"]: record for record in run["records"]}
    if len(results) != len(run["records"]):
        raise ValueError("duplicate rendering-stability reader result id")
    by_variant: dict[str, Counter] = {}
    findings = []
    for record in records.values():
        crop_path = output_dir / record["crop"]
        if _sha256(crop_path) != record["crop_sha256"]:
            raise ValueError(f"rendering-stability crop hash mismatch: {record['id']}")
        result = results.get(record["id"])
        if result is not None and result.get("input_sha256") != record["crop_sha256"]:
            raise ValueError(f"rendering-stability input hash mismatch: {record['id']}")
        value, refusal = _observation(result)
        outcome = heldout._outcome(
            value, refusal, record["expected"], record["expected_kind"]
        )
        by_variant.setdefault(record["variant"], Counter())[outcome] += 1
        if outcome != "agree":
            findings.append({
                "id": record["id"],
                "base_id": record["base_id"],
                "variant": record["variant"],
                "expected": record["expected"],
                "text": value,
                "score": (result or {}).get("score"),
                "outcome": outcome,
            })
    summaries = {
        variant: _summary(counter) for variant, counter in sorted(by_variant.items())
    }
    report = {
        "schema_version": 1,
        "method": manifest["method"],
        "contract": manifest["contract"],
        "manifest_sha256": _sha256(manifest_path),
        "run_sha256": _sha256(run_path),
        "reader": run["reader"],
        "cells": manifest["cells"],
        "variants": manifest["variants"],
        "crops": manifest["crops"],
        "render_reproducibility": {
            "checked_300dpi": sum(record["dpi"] == 300 for record in manifest["renders"]),
            "exact_300dpi": sum(
                record["dpi"] == 300 and record["matches_reference_300dpi"]
                for record in manifest["renders"]
            ),
            "deskewed_renders": sum(bool(record["deskew_degrees"]) for record in manifest["renders"]),
            "unique_crop_hashes": len({
                record["crop_sha256"] for record in manifest["records"]
            }),
        },
        "variant_results": summaries,
        "best_agreement": max(summary["agree"] for summary in summaries.values()),
        "zero_disagreement_variants": sum(
            summary["disagree"] == 0 for summary in summaries.values()
        ),
        "instability_prediction": _prediction_summary(
            list(records.values()), results
        ),
        "findings": findings,
    }
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def _checked_result(report: dict) -> dict:
    checked = {
        key: report[key]
        for key in (
            "manifest_sha256",
            "run_sha256",
            "reader",
            "cells",
            "variants",
            "crops",
            "render_reproducibility",
            "variant_results",
            "best_agreement",
            "zero_disagreement_variants",
        )
    }
    checked["instability_prediction"] = {
        key: value
        for key, value in report["instability_prediction"].items()
        if key != "cells"
    }
    return checked


def check_corpus(root: Path, corpus_path: Path, report: dict) -> bool:
    corpus = json.loads(corpus_path.read_text())
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported rendering-stability corpus schema_version")
    for name, artifact in corpus["artifacts"].items():
        if _sha256(root / artifact["path"]) != artifact["sha256"]:
            raise ValueError(f"rendering-stability artifact hash mismatch: {name}")
    return _checked_result(report) == corpus["expected"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    prepare_parser.add_argument("--source-corpus", type=Path, default=DEFAULT_SOURCE_CORPUS)
    prepare_parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    prepare_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("run", type=Path)
    compare_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    compare_parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    compare_parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        manifest = prepare(args.labels, args.source_corpus, args.reference, args.output)
        print(f"prepared {manifest['crops']} crops across {manifest['variants']} variants")
        return
    report = compare(args.output, args.run)
    print(
        f"rendering stability: best {report['best_agreement']}/{report['cells']}; "
        f"zero-disagreement variants {report['zero_disagreement_variants']}/{report['variants']}"
    )
    if args.check and not check_corpus(ROOT, args.corpus, report):
        raise SystemExit("rendering-stability corpus differs from expected results")


if __name__ == "__main__":
    main()
