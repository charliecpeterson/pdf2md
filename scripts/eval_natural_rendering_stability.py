"""Test rendering instability against natural primary OCR errors and clean controls."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageOps

from pdf2md.render import CropRenderer
from pdf2md.scan_deskew import deskew_image
from pdf2md.schema import BBox


ROOT = Path(__file__).parent.parent
DEFAULT_CROPS = ROOT / "out" / "reviews" / "non-fischer-third-reader-v1" / "crops.json"
DEFAULT_CONFIDENCE = ROOT / "out" / "reviews" / "numeric-confidence-v1" / "report.json"
DEFAULT_BASELINE = ROOT / "out" / "reviews" / "rendering-stability-v1" / "report.json"
DEFAULT_OUTPUT = ROOT / "out" / "reviews" / "rendering-stability-natural-errors-v1"
DEFAULT_CORPUS = ROOT / "tests" / "natural_rendering_stability_corpus.json"

STABILITY_SPEC = importlib.util.spec_from_file_location(
    "eval_rendering_stability", ROOT / "scripts" / "eval_rendering_stability.py"
)
stability = importlib.util.module_from_spec(STABILITY_SPEC)
STABILITY_SPEC.loader.exec_module(stability)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")


def _document_versions(crop_manifest: dict) -> dict[str, dict]:
    documents = {}
    for document in crop_manifest["labels"]["documents"]:
        source_sha = document["source_sha256"]
        version_dir = ROOT / document["out_dir"] / source_sha[:16] / document["version"]
        documents[source_sha] = {**document, "version_dir": version_dir}
    return documents


def _render_table(document: dict, block_id: str, dpi: int, path: Path) -> dict:
    provenance_path = document["version_dir"] / "provenance.json"
    provenance = json.loads(provenance_path.read_text())
    table = next(item for item in provenance["tables"] if item["block_id"] == block_id)
    block = next(item for item in provenance["blocks"] if item["id"] == block_id)
    bbox = table.get("bbox") or block.get("bbox")
    if bbox is None:
        raise ValueError(f"natural-error table bbox missing: {block_id}")
    source_path = ROOT / document["source"]
    if _sha256(source_path) != document["source_sha256"]:
        raise ValueError(f"natural-error source hash mismatch: {document['id']}")
    with CropRenderer(source_path, dpi=dpi, padding_pts=6.0) as renderer:
        renderer.crop(int(table["page"]), BBox(**bbox), path, dpi=dpi)
    return {
        "provenance": provenance_path.relative_to(ROOT).as_posix(),
        "provenance_sha256": _sha256(provenance_path),
        "source": document["source"],
        "source_sha256": document["source_sha256"],
        "page": table["page"],
        "bbox": bbox,
    }


def _padded(box: list[int], image_size: tuple[int, int]) -> list[int]:
    return [
        max(0, box[0] - 8),
        max(0, box[1] - 5),
        min(image_size[0], box[2] + 8),
        min(image_size[1], box[3] + 5),
    ]


def _primary_values(confidence_path: Path) -> dict[tuple[str, str, int, int], str]:
    report = json.loads(confidence_path.read_text())
    return {
        (
            record["source_sha256"],
            record["block_id"],
            int(record["row"]),
            int(record["column"]),
        ): record["primary_value"]
        for record in report["records"]["natural"]
    }


def prepare(crops_path: Path, confidence_path: Path, output_dir: Path) -> dict:
    crop_manifest = json.loads(crops_path.read_text())
    documents = _document_versions(crop_manifest)
    primary_values = _primary_values(confidence_path)
    records_by_table: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in crop_manifest["crops"]:
        records_by_table[record["source_sha256"], record["block_id"]].append(record)

    render_dir = output_dir / "renders"
    crop_dir = output_dir / "crops"
    render_dir.mkdir(parents=True, exist_ok=True)
    crop_dir.mkdir(parents=True, exist_ok=True)
    prepared = []
    render_records = []
    variants = list(itertools.product(
        stability.DPIS,
        stability.PIXEL_MODES,
        stability.DESKEW_MODES,
        stability.PADDING_MODES,
    ))
    total = len(crop_manifest["crops"]) * len(variants)
    completed = 0
    for table_index, ((source_sha, block_id), base_records) in enumerate(
        sorted(records_by_table.items()), start=1
    ):
        print(f"[{table_index}/{len(records_by_table)}] {block_id}", flush=True)
        document = documents[source_sha]
        source_crop = ROOT / base_records[0]["source_crop"]
        if _sha256(source_crop) != base_records[0]["source_crop_sha256"]:
            raise ValueError(f"natural-error source crop hash mismatch: {block_id}")
        with Image.open(source_crop) as image:
            base_size = image.size
        for dpi in stability.DPIS:
            stem = base_records[0]["id"].split(":", 1)[0]
            block_stem = block_id.strip("#/").replace("/", "_")
            render_path = render_dir / f"{stem}-{block_stem}-dpi{dpi}.png"
            source_evidence = _render_table(document, block_id, dpi, render_path)
            with Image.open(render_path) as rendered:
                original = rendered.convert("RGB")
            corrected, angle = deskew_image(original)
            images = {"original": original, "deskewed": corrected}
            render_records.append({
                "source_sha256": source_sha,
                "block_id": block_id,
                "dpi": dpi,
                "path": render_path.relative_to(output_dir).as_posix(),
                "sha256": _sha256(render_path),
                "size": list(original.size),
                "deskew_degrees": angle,
                "matches_reference_300dpi": False,
                **source_evidence,
            })
            for record in base_records:
                tight = record["source_box"]
                boxes = {"tight": tight, "padded": _padded(tight, base_size)}
                key = (
                    source_sha,
                    block_id,
                    int(record["source_row"]),
                    int(record["source_column"]),
                )
                if key not in primary_values:
                    raise ValueError(f"natural-error primary value unavailable: {record['id']}")
                for pixels, deskew, padding in itertools.product(
                    stability.PIXEL_MODES,
                    stability.DESKEW_MODES,
                    stability.PADDING_MODES,
                ):
                    variant = stability._variant_id(dpi, pixels, deskew, padding)
                    box = stability._scaled_box(boxes[padding], base_size, original.size)
                    crop = images[deskew].crop(box)
                    processed = (
                        ImageOps.autocontrast(crop.convert("L"))
                        if pixels == "grayscale"
                        else stability._adaptive_binary(crop, dpi)
                    )
                    crop_path = crop_dir / f"{_safe_id(record['id'])}-{variant}.png"
                    processed.save(crop_path)
                    processed.close()
                    crop.close()
                    prepared.append({
                        "id": f"{record['id']}|{variant}",
                        "base_id": record["id"],
                        "panel_id": f"{source_sha[:16]}:{block_id}",
                        "document": document["id"],
                        "class": "natural_primary_error",
                        "expected": record["expected"],
                        "expected_kind": "numeric",
                        "primary": primary_values[key],
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
        "method": "factorial_rendering_stability_natural_errors",
        "contract": {
            "design": {
                "dpi": list(stability.DPIS),
                "pixels": list(stability.PIXEL_MODES),
                "deskew": list(stability.DESKEW_MODES),
                "padding": list(stability.PADDING_MODES),
            },
            "reader": "same pinned PP-OCRv6_medium_rec as the clean-control frame",
            "geometry": "human-verified source-pixel cell boxes fixed before recognition",
            "baseline_variant": stability.BASELINE_VARIANT,
        },
        "natural_crops_sha256": _sha256(crops_path),
        "numeric_confidence_sha256": _sha256(confidence_path),
        "cells": len(crop_manifest["crops"]),
        "variants": len(variants),
        "crops": len(prepared),
        "renders": render_records,
        "records": prepared,
    }
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


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 8) if denominator else None


def _combined_prediction(
    base_report: dict, natural_report: dict, natural_records: list[dict]
) -> dict:
    base_cells = base_report["instability_prediction"]["cells"]
    natural_cells = natural_report["instability_prediction"]["cells"]
    cells = [*base_cells, *natural_cells]
    counts = Counter()
    by_document: dict[str, Counter] = defaultdict(Counter)
    natural_documents = {
        record["base_id"]: record["document"] for record in natural_records
    }
    for cell in cells:
        unstable = cell["unstable_off_baseline"]
        primary_error = cell["base_id"] in natural_documents
        counts["unstable" if unstable else "stable"] += 1
        counts[
            f"{'unstable' if unstable else 'stable'}_"
            f"{'primary_error' if primary_error else 'clean_primary'}"
        ] += 1
        document = natural_documents.get(cell["base_id"])
        if document is not None:
            by_document[document]["errors"] += 1
            by_document[document]["unstable_errors"] += unstable
    error_cells = counts["unstable_primary_error"] + counts["stable_primary_error"]
    clean_cells = counts["unstable_clean_primary"] + counts["stable_clean_primary"]
    return {
        "cells": len(cells),
        "natural_primary_errors": error_cells,
        "clean_primary_controls": clean_cells,
        "unstable_cells": counts["unstable"],
        "stable_cells": counts["stable"],
        "unstable_primary_errors": counts["unstable_primary_error"],
        "stable_primary_errors": counts["stable_primary_error"],
        "unstable_clean_primary": counts["unstable_clean_primary"],
        "stable_clean_primary": counts["stable_clean_primary"],
        "primary_error_sensitivity": _rate(counts["unstable_primary_error"], error_cells),
        "primary_error_specificity": _rate(counts["stable_clean_primary"], clean_cells),
        "primary_error_positive_predictive_value": _rate(
            counts["unstable_primary_error"], counts["unstable"]
        ),
        "primary_error_negative_predictive_value": _rate(
            counts["stable_clean_primary"], counts["stable"]
        ),
        "by_natural_error_document": {
            document: {
                "errors": document_counts["errors"],
                "unstable_errors": document_counts["unstable_errors"],
            }
            for document, document_counts in sorted(by_document.items())
        },
    }


def compare(output_dir: Path, run_path: Path, baseline_path: Path) -> dict:
    natural_report = stability.compare(output_dir, run_path)
    base_report = json.loads(baseline_path.read_text())
    natural_records = json.loads((output_dir / "manifest.json").read_text())["records"]
    if natural_report["reader"] != base_report["reader"]:
        raise ValueError("natural and clean rendering frames use different readers")
    report = {
        "schema_version": 1,
        "method": "natural_error_rendering_instability_prediction",
        "contract": {
            "primary_error_labels": (
                "the 14 natural cells are source-labelled primary errors; the 56 "
                "existing cells are source-labelled clean primary controls"
            ),
            "instability": (
                "distinct accepted-or-refused signatures across 23 off-baseline "
                "rendering variants; the baseline does not define instability"
            ),
            "authority": "ranking evidence only; no value replacement",
        },
        "natural_manifest_sha256": natural_report["manifest_sha256"],
        "natural_run_sha256": natural_report["run_sha256"],
        "clean_control_report_sha256": _sha256(baseline_path),
        "reader": natural_report["reader"],
        "natural_cells": natural_report["cells"],
        "variants": natural_report["variants"],
        "natural_variant_results": natural_report["variant_results"],
        "natural_instability_prediction": natural_report["instability_prediction"],
        "records": natural_records,
        "combined_instability_prediction": _combined_prediction(
            base_report, natural_report, natural_records
        ),
    }
    (output_dir / "combined-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def _checked_result(report: dict) -> dict:
    return {
        "natural_manifest_sha256": report["natural_manifest_sha256"],
        "natural_run_sha256": report["natural_run_sha256"],
        "clean_control_report_sha256": report["clean_control_report_sha256"],
        "reader": report["reader"],
        "natural_cells": report["natural_cells"],
        "variants": report["variants"],
        "natural_variant_results_sha256": _json_sha256(
            report["natural_variant_results"]
        ),
        "natural_instability_prediction": {
            key: value
            for key, value in report["natural_instability_prediction"].items()
            if key != "cells"
        },
        "combined_instability_prediction": report["combined_instability_prediction"],
    }


def check_corpus(root: Path, corpus_path: Path, report: dict) -> bool:
    corpus = json.loads(corpus_path.read_text())
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported natural rendering stability schema_version")
    for name, artifact in corpus["artifacts"].items():
        if _sha256(root / artifact["path"]) != artifact["sha256"]:
            raise ValueError(f"natural rendering artifact hash mismatch: {name}")
    if _checked_result(report) != corpus["expected"]:
        raise ValueError("natural rendering stability differs from frozen corpus")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--crops", type=Path, default=DEFAULT_CROPS)
    prepare_parser.add_argument("--confidence", type=Path, default=DEFAULT_CONFIDENCE)
    prepare_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("run", type=Path)
    compare_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    compare_parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    compare_parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    compare_parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        manifest = prepare(args.crops, args.confidence, args.output)
        print(f"prepared {manifest['crops']} crops across {manifest['variants']} variants")
        return 0
    report = compare(args.output, args.run, args.baseline)
    prediction = report["combined_instability_prediction"]
    print(
        "natural rendering instability: "
        f"{prediction['unstable_primary_errors']}/{prediction['natural_primary_errors']} "
        "errors unstable; "
        f"{prediction['unstable_clean_primary']}/{prediction['clean_primary_controls']} "
        "clean controls unstable"
    )
    if args.check:
        check_corpus(ROOT, args.corpus, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
