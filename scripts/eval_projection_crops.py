"""Build and compare source-pixel cell crops against the OCR-derived crop path."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from pdf2md.line_reader import MINIMUM_SCORE, PINNED_READER, _sha256, _validate_reader
from pdf2md.row_locator import projection_cell_box
from pdf2md.table_verify import numeric_values_equal


ROOT = Path(__file__).parent.parent
DEFAULT_RECOVERY = ROOT / "out" / "reviews" / "fischer-source-row-recovery-v2"
DEFAULT_OUTPUT = ROOT / "out" / "reviews" / "fischer-projection-crops-v1"
DEFAULT_LABELS = ROOT / "tests" / "fischer_source_row_recovery_labels.json"
DEFAULT_ADJUDICATIONS = ROOT / "tests" / "fischer_projection_crop_labels.json"
DEFAULT_CORPUS = ROOT / "tests" / "projection_crop_corpus.json"


def _reference_sha256(records: list[dict]) -> str:
    reference = [
        {
            key: record[key]
            for key in (
                "id",
                "source_crop_sha256",
                "source_box",
                "projection_x_run",
                "crop_sha256",
            )
        }
        for record in records
    ]
    return hashlib.sha256(
        json.dumps(reference, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _reader_crop(image: Image.Image, box: list[int]) -> Image.Image:
    crop = ImageOps.autocontrast(ImageOps.grayscale(image.crop(tuple(box))))
    crop = crop.resize((crop.width * 4, crop.height * 4), Image.Resampling.LANCZOS)
    crop = crop.filter(ImageFilter.UnsharpMask(radius=1, percent=100, threshold=3))
    canvas = Image.new("RGB", (max(640, crop.width), max(192, crop.height)), "white")
    canvas.paste(
        crop,
        ((canvas.width - crop.width) // 2, (canvas.height - crop.height) // 2),
    )
    crop.close()
    return canvas


def prepare(recovery_dir: Path, output_dir: Path) -> dict:
    recovery_dir = recovery_dir.resolve()
    recovery_manifest_path = recovery_dir / "manifest.json"
    old_run_path = recovery_dir / "run.json"
    recovery = json.loads(recovery_manifest_path.read_text())
    old_run = json.loads(old_run_path.read_text())
    _validate_reader(old_run.get("reader", {}))

    panels = {
        (panel["source_block_id"], int(panel["panel"])): panel
        for panel in recovery["panels"]
    }
    crop_dir = output_dir / "crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    records = []
    by_source: dict[str, list[dict]] = {}
    for record in recovery["records"]:
        by_source.setdefault(record["source_crop"], []).append(record)

    total = len(recovery["records"])
    prepared_count = 0
    for source_index, (source_name, source_records) in enumerate(
        sorted(by_source.items()), start=1
    ):
        source_path = recovery_dir / source_name
        expected_hashes = {record["source_crop_sha256"] for record in source_records}
        if len(expected_hashes) != 1 or _sha256(source_path) != expected_hashes.pop():
            raise ValueError(f"source crop hash mismatch: {source_name}")
        print(
            f"[{source_index}/{len(by_source)}] {source_name}: "
            f"{len(source_records)} cells",
            flush=True,
        )
        with Image.open(source_path) as image:
            for old in source_records:
                panel = panels[old["source_block_id"], int(old["panel"])]
                projection = panel.get("projection")
                if projection is None:
                    raise ValueError(f"projection rows missing: {old['id']}")
                row_band = projection["bands"][int(old["source_position"])]
                box = projection_cell_box(
                    old["projection_x_run"], row_band, image.size
                )
                if box[0] >= box[2] or box[1] >= box[3]:
                    raise ValueError(f"empty projection crop: {old['id']}")
                crop_path = crop_dir / Path(old["crop"]).name
                crop = _reader_crop(image, box)
                crop.save(crop_path)
                crop.close()
                records.append({
                    "id": old["id"],
                    "role": old["role"],
                    "page": old["page"],
                    "source_block_id": old["source_block_id"],
                    "panel": old["panel"],
                    "source_position": old["source_position"],
                    "source_column": old["source_column"],
                    "template_key": old["template_key"],
                    "raw_value": old.get("raw_value"),
                    "source_crop": str(source_path),
                    "source_crop_sha256": old["source_crop_sha256"],
                    "reference_box": old["source_box"],
                    "reference_crop": str((recovery_dir / old["crop"]).resolve()),
                    "reference_crop_sha256": old["crop_sha256"],
                    "projection_x_run": old["projection_x_run"],
                    "projection_row_band": row_band,
                    "projection_box": box,
                    "crop": crop_path.relative_to(output_dir).as_posix(),
                    "crop_sha256": _sha256(crop_path),
                })
                prepared_count += 1
                if prepared_count % 100 == 0 or prepared_count == total:
                    print(f"  prepared {prepared_count}/{total}", flush=True)

    manifest = {
        "schema_version": 1,
        "method": "projection_derived_cell_crop_differential",
        "contract": {
            "geometry": (
                "projection column ink run and projection row ink band only; no OCR "
                "word boxes or reader output"
            ),
            "padding": (
                "x=max(4, round(row_height*0.25)); "
                "y=max(2, round(row_height*0.15))"
            ),
            "preprocessing": (
                "grayscale autocontrast, 4x Lanczos, unsharp mask, centered on the "
                "same 640x192 minimum white canvas as the reference crop path"
            ),
            "comparison": (
                "the old crop reader is a differential reference, not ground truth; "
                "source-reviewed labels are the accuracy reference"
            ),
        },
        "recovery_dir": str(recovery_dir),
        "recovery_manifest_sha256": _sha256(recovery_manifest_path),
        "reference_run_sha256": _sha256(old_run_path),
        "reference_geometry_sha256": _reference_sha256(recovery["records"]),
        "source_sha256": recovery["source_sha256"],
        "minimum_score": MINIMUM_SCORE,
        "pinned_reader": PINNED_READER,
        "records": records,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
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


def _observation(record: dict, result: dict | None) -> tuple[str | None, str | None]:
    if result is None:
        return None, "result_missing"
    if result.get("input_sha256") != record["crop_sha256"]:
        return None, "input_hash_mismatch"
    if result.get("error"):
        return None, "reader_error"
    text = str(result.get("text") or "").strip()
    if not text:
        return None, "reader_text_missing"
    return text, None


def _gated_observation(
    record: dict, result: dict | None
) -> tuple[str | None, str | None]:
    text, refusal = _observation(record, result)
    if refusal is not None:
        return text, refusal
    if result.get("score") is None:
        return text, "reader_score_missing"
    if float(result["score"]) < MINIMUM_SCORE:
        return text, "reader_score_below_threshold"
    return text, None


def _score_labels(
    labels: dict, records: dict[str, dict], results: dict[str, dict], *, gated: bool
) -> dict:
    counts = Counter()
    for label in labels["records"]:
        record = records.get(label["id"])
        if record is None:
            counts["tool_refused"] += 1
            continue
        observation = _gated_observation if gated else _observation
        value, refusal = observation(record, results.get(label["id"]))
        if refusal is not None:
            counts["tool_refused"] += 1
        elif numeric_values_equal(value, label["expected"]):
            counts["agree"] += 1
        else:
            counts["disagree"] += 1
    return {
        "checked": len(labels["records"]),
        "agree": counts["agree"],
        "disagree": counts["disagree"],
        "tool_refused": counts["tool_refused"],
    }


def _score_fallback_labels(
    labels: dict,
    old_records: dict[str, dict],
    old_results: dict[str, dict],
    new_records: dict[str, dict],
    new_results: dict[str, dict],
) -> dict:
    counts = Counter()
    for label in labels["records"]:
        old_record = old_records.get(label["id"])
        new_record = new_records.get(label["id"])
        old_value, old_refusal = (
            _gated_observation(old_record, old_results.get(label["id"]))
            if old_record is not None else (None, "record_missing")
        )
        if old_refusal is None:
            value, refusal = old_value, None
        elif new_record is not None:
            value, refusal = _gated_observation(
                new_record, new_results.get(label["id"])
            )
        else:
            value, refusal = None, "record_missing"
        if refusal is not None:
            counts["tool_refused"] += 1
        elif numeric_values_equal(value, label["expected"]):
            counts["agree"] += 1
        else:
            counts["disagree"] += 1
    return {
        "checked": len(labels["records"]),
        "agree": counts["agree"],
        "disagree": counts["disagree"],
        "tool_refused": counts["tool_refused"],
    }


def _reader_matches(
    record: dict, results: dict[str, dict], expected: str | None
) -> bool:
    if expected is None:
        return False
    value, refusal = _gated_observation(record, results.get(record["id"]))
    return refusal is None and numeric_values_equal(value, expected)


def _overlay_summary(
    records: list[dict],
    old_results: dict[str, dict],
    new_results: dict[str, dict] | None,
    new_records: dict[str, dict] | None = None,
) -> dict:
    rows: dict[tuple[str, int, int], list[dict]] = {}
    for record in records:
        row_id = (
            record["source_block_id"],
            int(record["panel"]),
            int(record["source_position"]),
        )
        rows.setdefault(row_id, []).append(record)

    key_confirmed = {}
    for row_id, cells in rows.items():
        key = min(cells, key=lambda cell: int(cell["source_column"]))
        key_confirmed[row_id] = _reader_matches(
            key, old_results, key["template_key"]
        ) or (
            new_results is not None
            and new_records is not None
            and _reader_matches(
                new_records[key["id"]], new_results, key["template_key"]
            )
        )
    alignment_confirmed = {
        row_id: key_confirmed[row_id]
        for row_id, cells in rows.items()
        if cells[0].get("alignment_position") == row_id[2]
    }

    counts = Counter()
    control_counts = Counter()
    divergences = []
    confirmed_rows = Counter()
    for row_id, cells in rows.items():
        key = min(cells, key=lambda cell: int(cell["source_column"]))
        alignment_position = key.get("alignment_position")
        alignment_ok = (
            alignment_position is None
            or alignment_confirmed.get(
                (row_id[0], row_id[1], int(alignment_position)), False
            )
        )
        row_ok = key_confirmed[row_id] and alignment_ok
        if row_ok:
            confirmed_rows[key["role"]] += 1
        for cell in cells:
            is_key = cell["id"] == key["id"]
            cell_ok = row_ok and (
                is_key
                or _reader_matches(cell, old_results, cell["tesseract_value"])
                or (
                    new_results is not None
                    and new_records is not None
                    and _reader_matches(
                        new_records[cell["id"]],
                        new_results,
                        cell["tesseract_value"],
                    )
                )
            )
            counts[f"{cell['role']}_{'accepted' if cell_ok else 'refused'}"] += 1
            if cell["role"] == "control":
                if not cell_ok:
                    control_counts["tool_refused"] += 1
                else:
                    candidate = cell["template_key"] if is_key else cell["tesseract_value"]
                    outcome = (
                        "agree"
                        if numeric_values_equal(candidate, cell.get("raw_value"))
                        else "disagree"
                    )
                    control_counts[outcome] += 1
                    if outcome == "disagree":
                        divergences.append({
                            "id": cell["id"],
                            "structured_value": cell.get("raw_value"),
                            "source_candidate": candidate,
                        })
    return {
        "confirmed_rows": dict(sorted(confirmed_rows.items())),
        "cell_counts": dict(sorted(counts.items())),
        "controls": {
            "checked": sum(control_counts.values()),
            "agree": control_counts["agree"],
            "disagree": control_counts["disagree"],
            "tool_refused": control_counts["tool_refused"],
        },
        "control_divergences": divergences,
    }


def compare(
    output_dir: Path,
    run_path: Path,
    labels_path: Path = DEFAULT_LABELS,
    adjudications_path: Path = DEFAULT_ADJUDICATIONS,
) -> dict:
    manifest = json.loads((output_dir / "manifest.json").read_text())
    recovery_dir = Path(manifest["recovery_dir"])
    recovery_manifest_path = recovery_dir / "manifest.json"
    old_run_path = recovery_dir / "run.json"
    if _sha256(recovery_manifest_path) != manifest["recovery_manifest_sha256"]:
        raise ValueError("recovery manifest changed after projection crop preparation")
    if _sha256(old_run_path) != manifest["reference_run_sha256"]:
        raise ValueError("reference reader run changed after projection crop preparation")

    recovery = json.loads(recovery_manifest_path.read_text())
    if _reference_sha256(recovery["records"]) != manifest["reference_geometry_sha256"]:
        raise ValueError("reference crop geometry changed")
    old_run = json.loads(old_run_path.read_text())
    new_run = json.loads(run_path.read_text())
    _validate_reader(old_run.get("reader", {}))
    _validate_reader(new_run.get("reader", {}))
    old_results = {record["id"]: record for record in old_run["records"]}
    new_results = {record["id"]: record for record in new_run["records"]}
    if len(old_results) != len(old_run["records"]):
        raise ValueError("duplicate reference reader result id")
    if len(new_results) != len(new_run["records"]):
        raise ValueError("duplicate projection reader result id")

    old_records = {record["id"]: record for record in recovery["records"]}
    new_records = {record["id"]: record for record in manifest["records"]}
    counts = Counter()
    transitions = Counter()
    findings = []
    for sample_id, new_record in new_records.items():
        crop_path = output_dir / new_record["crop"]
        if not crop_path.is_file() or _sha256(crop_path) != new_record["crop_sha256"]:
            raise ValueError(f"projection crop hash mismatch: {sample_id}")
        old_record = old_records[sample_id]
        old_value, old_refusal = _observation(old_record, old_results.get(sample_id))
        new_value, new_refusal = _observation(new_record, new_results.get(sample_id))
        if old_refusal is not None:
            outcome = "no_reference"
        elif new_refusal is not None:
            outcome = "tool_refused"
        elif numeric_values_equal(old_value, new_value):
            outcome = "agree"
        else:
            outcome = "disagree"
        counts[outcome] += 1

        _, old_gate_refusal = _gated_observation(
            old_record, old_results.get(sample_id)
        )
        _, new_gate_refusal = _gated_observation(
            new_record, new_results.get(sample_id)
        )
        old_gate = "accepted" if old_gate_refusal is None else "refused"
        new_gate = "accepted" if new_gate_refusal is None else "refused"
        transitions[f"{old_gate}_to_{new_gate}"] += 1
        if outcome not in {"agree", "no_reference"} or old_gate != new_gate:
            old_result = old_results.get(sample_id) or {}
            new_result = new_results.get(sample_id) or {}
            findings.append({
                "id": sample_id,
                "outcome": outcome,
                "old_text": old_value,
                "old_score": old_result.get("score"),
                "old_refusal": old_refusal,
                "new_text": new_value,
                "new_score": new_result.get("score"),
                "new_refusal": new_refusal,
                "old_gate": old_gate,
                "new_gate": new_gate,
            })

    labels = json.loads(labels_path.read_text())
    if labels.get("source_sha256") != manifest["source_sha256"]:
        raise ValueError("source label hash mismatch")
    for label in labels["records"]:
        if old_records[label["id"]]["crop_sha256"] != label["crop_sha256"]:
            raise ValueError(f"source label crop hash mismatch: {label['id']}")

    adjudications = json.loads(adjudications_path.read_text())
    if adjudications.get("source_sha256") != manifest["source_sha256"]:
        raise ValueError("projection adjudication source hash mismatch")
    for label in adjudications["records"]:
        old_record = old_records[label["id"]]
        new_record = new_records[label["id"]]
        if old_record["crop_sha256"] != label["reference_crop_sha256"]:
            raise ValueError(f"adjudication reference hash mismatch: {label['id']}")
        if new_record["crop_sha256"] != label["projection_crop_sha256"]:
            raise ValueError(f"adjudication projection hash mismatch: {label['id']}")

    report = {
        "schema_version": 1,
        "method": "projection_derived_cell_crop_differential",
        "contract": manifest["contract"],
        "source_sha256": manifest["source_sha256"],
        "reader": new_run["reader"],
        "cells": len(new_records),
        "reader_parity": {
            "checked": len(new_records) - counts["no_reference"],
            "agree": counts["agree"],
            "disagree": counts["disagree"],
            "tool_refused": counts["tool_refused"],
            "no_reference": counts["no_reference"],
        },
        "score_gate_transitions": dict(sorted(transitions.items())),
        "source_labels": {
            "old_raw": _score_labels(
                labels, old_records, old_results, gated=False
            ),
            "projection_raw": _score_labels(
                labels, new_records, new_results, gated=False
            ),
            "old_at_threshold": _score_labels(
                labels, old_records, old_results, gated=True
            ),
            "projection_at_threshold": _score_labels(
                labels, new_records, new_results, gated=True
            ),
            "fallback_at_threshold": _score_fallback_labels(
                labels, old_records, old_results, new_records, new_results
            ),
        },
        "projection_adjudications": {
            "reference_at_threshold": _score_labels(
                adjudications, old_records, old_results, gated=True
            ),
            "projection_at_threshold": _score_labels(
                adjudications, new_records, new_results, gated=True
            ),
            "fallback_at_threshold": _score_fallback_labels(
                adjudications, old_records, old_results, new_records, new_results
            ),
        },
        "overlay": {
            "reference_only": _overlay_summary(
                recovery["records"], old_results, None
            ),
            "projection_fallback": _overlay_summary(
                recovery["records"], old_results, new_results, new_records
            ),
        },
        "findings": findings,
    }
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def _report_summary(report: dict) -> dict:
    return {
        key: report[key]
        for key in (
            "reader_parity",
            "score_gate_transitions",
            "source_labels",
            "projection_adjudications",
            "overlay",
        )
    }


def check(
    corpus_path: Path,
    output_dir: Path,
    run_path: Path,
    labels_path: Path,
    adjudications_path: Path,
    report: dict,
) -> None:
    corpus = json.loads(corpus_path.read_text())
    manifest = json.loads((output_dir / "manifest.json").read_text())
    paths = {
        "recovery_manifest_sha256": (
            Path(manifest["recovery_dir"]) / "manifest.json"
        ),
        "projection_manifest_sha256": output_dir / "manifest.json",
        "projection_run_sha256": run_path,
        "source_labels_sha256": labels_path,
        "projection_adjudications_sha256": adjudications_path,
    }
    mismatches = [
        key for key, path in paths.items()
        if corpus.get(key) != _sha256(path)
    ]
    if report["source_sha256"] != corpus.get("source_sha256"):
        mismatches.append("source_sha256")
    if mismatches:
        mismatch_names = ", ".join(mismatches)
        raise ValueError(f"projection crop corpus hash mismatch: {mismatch_names}")
    actual = _report_summary(report)
    if actual != corpus.get("expected"):
        print(json.dumps({"expected": corpus.get("expected"), "actual": actual}, indent=2))
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--recovery", type=Path, default=DEFAULT_RECOVERY)
    prepare_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("run", type=Path)
    compare_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    compare_parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    compare_parser.add_argument(
        "--adjudications", type=Path, default=DEFAULT_ADJUDICATIONS
    )
    compare_parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    compare_parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if args.command == "prepare":
        report = prepare(args.recovery, args.output)
        print(f"projection crops: prepared {len(report['records'])} cells")
    else:
        report = compare(args.output, args.run, args.labels, args.adjudications)
        if args.check:
            check(
                args.corpus,
                args.output,
                args.run,
                args.labels,
                args.adjudications,
                report,
            )
        parity = report["reader_parity"]
        labels = report["source_labels"]["projection_at_threshold"]
        print(
            f"projection crops: {parity['agree']}/{parity['checked']} reader reads "
            f"agree; labels {labels['agree']}/{labels['checked']} agree, "
            f"{labels['disagree']} disagree, {labels['tool_refused']} refused"
        )


if __name__ == "__main__":
    main()
