"""Compare primary and auxiliary reads on source-labelled held-out data cells."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PIL import Image

from pdf2md.line_reader import (
    MINIMUM_SCORE,
    _run_tesseract,
    _service_crop,
    _sha256,
    _table_crop,
    _validate_reader,
)
from pdf2md.row_locator import (
    projection_cell_box,
    projection_lane_run,
    projection_panel_bounds,
    projection_row_bands,
)
from pdf2md.table_verify import _numeric_read
from pdf2md.tables import gfm_rows


ROOT = Path(__file__).parent.parent
DEFAULT_LABELS = ROOT / "tests" / "heldout_data_cell_labels.json"
DEFAULT_READER_CORPUS = ROOT / "tests" / "heldout_data_reader_corpus.json"
DEFAULT_SOURCE_CORPUS = ROOT / "tests" / "source_row_alignment_corpus.json"
DEFAULT_ALIGNMENT = ROOT / "out" / "reviews" / "source-row-alignment-heldout-v1"
DEFAULT_OUTPUT = ROOT / "out" / "reviews" / "heldout-data-reader-v1"

RECOVERY_SPEC = importlib.util.spec_from_file_location(
    "eval_source_row_recovery", ROOT / "scripts" / "eval_source_row_recovery.py"
)
recovery = importlib.util.module_from_spec(RECOVERY_SPEC)
RECOVERY_SPEC.loader.exec_module(recovery)

KEY_READER_SPEC = importlib.util.spec_from_file_location(
    "eval_heldout_key_reader", ROOT / "scripts" / "eval_heldout_key_reader.py"
)
key_reader = importlib.util.module_from_spec(KEY_READER_SPEC)
KEY_READER_SPEC.loader.exec_module(key_reader)

NUMBER_PATTERN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?"
FORMATTED_NUMBER = re.compile(
    rf"^\$?({NUMBER_PATTERN})(?:\^[A-Za-z]|[A-Za-z°?\"'])?\$?$"
)


def _numeric_value(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    normalized = _numeric_read(str(raw))
    match = FORMATTED_NUMBER.fullmatch(normalized)
    if match is None:
        return None
    try:
        return Decimal(match.group(1))
    except InvalidOperation:
        return None


def _placeholder_value(raw: str | None) -> str | None:
    if raw is None:
        return None
    normalized = str(raw).strip().lower().replace("$", "")
    if "vdots" in normalized or (
        normalized and all(character in ".:·⋮" for character in normalized)
    ):
        return "vertical_ellipsis"
    return None


def _semantic_value(raw: str | None, expected_kind: str) -> Decimal | str | None:
    if expected_kind == "numeric":
        return _numeric_value(raw)
    if expected_kind == "placeholder":
        return _placeholder_value(raw)
    raise ValueError(f"unsupported held-out data expected kind: {expected_kind}")


def _outcome(
    raw: str | None,
    refusal: str | None,
    expected: str,
    expected_kind: str = "numeric",
) -> str:
    actual = _semantic_value(raw, expected_kind)
    if refusal is not None:
        return "tool_refused"
    if actual is None:
        if expected_kind == "placeholder" and _numeric_value(raw) is not None:
            return "disagree"
        return "tool_refused"
    if expected_kind == "numeric":
        target = _semantic_value(expected, expected_kind)
        if target is None:
            raise ValueError(f"invalid held-out expected value: {expected}")
    else:
        target = expected
    return "agree" if actual == target else "disagree"


def _fallback_observation(
    reference: tuple[str | None, float | None, str | None],
    projection: tuple[str | None, float | None, str | None],
    expected_kind: str = "numeric",
) -> tuple[str | None, float | None, str | None, str | None]:
    for reader, observation in (("reference", reference), ("projection", projection)):
        value, score, refusal = observation
        if refusal is None and _semantic_value(value, expected_kind) is not None:
            return value, score, None, reader
    return None, None, "reader_unavailable", None


def _summary(counter: Counter) -> dict:
    return {
        "checked": sum(counter.values()),
        "agree": counter["agree"],
        "disagree": counter["disagree"],
        "tool_refused": counter["tool_refused"],
    }


def _checked_result(report: dict) -> dict:
    return {
        **{
            key: report[key]
            for key in (
                "manifest_sha256",
                "reference_run_sha256",
                "projection_run_sha256",
                "reader",
                "documents",
                "panels",
                "cells",
                "primary",
                "tesseract",
                "reference",
                "projection",
                "fallback",
                "confirmed",
                "fallback_readers",
                "panel_results",
            )
        },
        "findings": [
            {
                "id": finding["id"],
                "fallback_reader": finding["fallback_reader"],
                "outcomes": finding["outcomes"],
            }
            for finding in report["findings"]
        ],
    }


def check_corpus(root: Path, corpus_path: Path, report: dict) -> bool:
    corpus = json.loads(corpus_path.read_text())
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported held-out data reader corpus schema_version")
    for name, artifact in corpus["artifacts"].items():
        if _sha256(root / artifact["path"]) != artifact["sha256"]:
            raise ValueError(f"held-out data reader artifact hash mismatch: {name}")
    return _checked_result(report) == corpus.get("expected")


def prepare(
    labels_path: Path,
    source_corpus_path: Path,
    alignment_dir: Path,
    output_dir: Path,
    tesseract: str,
) -> dict:
    labels = json.loads(labels_path.read_text())
    source_corpus = json.loads(source_corpus_path.read_text())
    alignment = json.loads((alignment_dir / "report.json").read_text())
    if labels.get("schema_version") != 1:
        raise ValueError("unsupported held-out data label schema_version")
    if source_corpus.get("schema_version") != 1:
        raise ValueError("unsupported source-row corpus schema_version")
    if alignment.get("corpus_sha256") != _sha256(source_corpus_path):
        raise ValueError("held-out alignment corpus hash mismatch")

    source_panels = {panel["id"]: panel for panel in source_corpus["panels"]}
    aligned_panels = {
        panel["id"]: panel for panel in alignment["panels_report"]
    }
    reference_dir = output_dir / "reference-crops"
    projection_dir = output_dir / "projection-crops"
    reference_dir.mkdir(parents=True, exist_ok=True)
    projection_dir.mkdir(parents=True, exist_ok=True)
    records = []

    for panel_index, labelled_panel in enumerate(labels["panels"], start=1):
        panel_id = labelled_panel["id"]
        print(f"[{panel_index}/{len(labels['panels'])}] {panel_id}", flush=True)
        panel = source_panels.get(panel_id, labelled_panel.get("source_panel"))
        if panel is None:
            raise ValueError(f"held-out source panel unavailable: {panel_id}")
        aligned = aligned_panels.get(panel_id)

        provenance_path = ROOT / panel["version_dir"] / "provenance.json"
        if _sha256(provenance_path) != panel["provenance_sha256"]:
            raise ValueError(f"provenance hash mismatch: {panel_id}")
        provenance = json.loads(provenance_path.read_text())
        source_table = next(
            table
            for table in provenance["tables"]
            if table["block_id"] == panel["block_id"]
        )
        structured_rows = gfm_rows(source_table["gfm"])
        if aligned is not None:
            source_crop = alignment_dir / aligned["source_crop"]
            tsv_path = alignment_dir / aligned["tesseract_tsv"]
            if _sha256(tsv_path) != aligned["tesseract_tsv_sha256"]:
                raise ValueError(f"Tesseract TSV hash mismatch: {panel_id}")
        else:
            source_path = ROOT / panel["source"]
            if _sha256(source_path) != panel["source_sha256"]:
                raise ValueError(f"source PDF hash mismatch: {panel_id}")
            source_dir = output_dir / "source-crops"
            tsv_dir = output_dir / "tesseract"
            source_dir.mkdir(parents=True, exist_ok=True)
            tsv_dir.mkdir(parents=True, exist_ok=True)
            source_crop = source_dir / f"{panel_id}.png"
            blocks = {block["id"]: block for block in provenance["blocks"]}
            _table_crop(
                source_path,
                source_table,
                blocks[panel["block_id"]],
                source_crop,
            )
            executable = shutil.which(tesseract)
            if executable is None:
                raise FileNotFoundError(f"Tesseract executable not found: {tesseract}")
            tsv_path = tsv_dir / f"{panel_id}.tsv"
            tsv_path.write_text(_run_tesseract(executable, source_crop))
        if _sha256(source_crop) != panel["source_crop_sha256"]:
            raise ValueError(f"source crop hash mismatch: {panel_id}")

        key_bounds = tuple(labelled_panel.get("key_bounds", panel.get("key_bounds", ())))
        expected_rows = int(labelled_panel.get("row_count", len(panel.get("keys", []))))
        lines = recovery._panel_lines(tsv_path.read_text(), key_bounds)
        if len(lines) != expected_rows:
            raise ValueError(f"source key line count mismatch: {panel_id}")

        with Image.open(source_crop) as image:
            panel_count = int(panel.get("projection_panel_count", 1))
            source_panel_index = int(panel.get("projection_panel_index", 0))
            projection_top = int(labelled_panel.get("projection_top", 0))
            projection_image = (
                image
                if projection_top == 0
                else image.crop((0, projection_top, image.width, image.height))
            )
            panel_bounds, _, panel_refusal = projection_panel_bounds(
                projection_image, panel_count
            )
            if panel_bounds is None:
                raise ValueError(f"projection panel refused {panel_id}: {panel_refusal}")
            bands, _, row_refusal = projection_row_bands(
                projection_image,
                expected_rows,
                panel_index=source_panel_index,
                panel_count=panel_count,
                stripe_fraction=float(
                    labelled_panel.get(
                        "projection_stripe_fraction",
                        panel["projection_stripe_fraction"],
                    )
                ),
                panel_bounds=panel_bounds,
            )
            if projection_image is not image:
                projection_image.close()
            if bands is None:
                raise ValueError(f"projection rows refused {panel_id}: {row_refusal}")
            bands = [
                (top + projection_top, bottom + projection_top)
                for top, bottom in bands
            ]

            for cell in labelled_panel["cells"]:
                position = int(cell["row_position"])
                column = int(cell["column"])
                lane = tuple(int(value) for value in labelled_panel["column_bounds"][str(column)])
                if position < 0 or position >= len(lines):
                    raise ValueError(f"row position outside panel: {panel_id}:{position}")
                structured_row = position + int(labelled_panel["structured_row_offset"])
                if structured_row >= len(structured_rows) or column >= len(structured_rows[structured_row]):
                    raise ValueError(f"structured cell unavailable: {panel_id}:{position}:{column}")

                line_words = lines[position][0]
                reference_words = recovery._cell_words(line_words, lane)
                if not reference_words:
                    raise ValueError(f"reference words unavailable: {panel_id}:{position}:{column}")
                reference_crop, reference_box = _service_crop(image, reference_words)
                stem = f"{panel_id}_r{position}_c{column}"
                reference_path = reference_dir / f"{stem}.png"
                reference_crop.save(reference_path)
                reference_crop.close()

                projection_run = projection_lane_run(image, bands[position], lane)
                if projection_run is None:
                    raise ValueError(f"projection lane empty: {panel_id}:{position}:{column}")
                projection_box = projection_cell_box(
                    projection_run, bands[position], image.size
                )
                projection_crop = recovery._cell_crop(image, tuple(projection_box))
                projection_path = projection_dir / f"{stem}.png"
                projection_crop.save(projection_path)
                projection_crop.close()

                record_id = f"{panel_id}:r{position}:c{column}"
                records.append({
                    "id": record_id,
                    "panel_id": panel_id,
                    "source": panel["source"],
                    "source_sha256": panel["source_sha256"],
                    "page": panel["page"],
                    "block_id": panel["block_id"],
                    "row_position": position,
                    "column": column,
                    "class": cell["class"],
                    "expected": cell["expected"],
                    "expected_kind": cell.get("expected_kind", "numeric"),
                    "primary": structured_rows[structured_row][column],
                    "tesseract": recovery._observed_text(reference_words),
                    "lane_bounds": list(lane),
                    "source_crop": str(source_crop.resolve()),
                    "source_crop_sha256": panel["source_crop_sha256"],
                    "reference_box": reference_box,
                    "reference_crop": reference_path.relative_to(output_dir).as_posix(),
                    "reference_crop_sha256": _sha256(reference_path),
                    "projection_row_band": list(bands[position]),
                    "projection_x_run": list(projection_run),
                    "projection_box": projection_box,
                    "projection_crop": projection_path.relative_to(output_dir).as_posix(),
                    "projection_crop_sha256": _sha256(projection_path),
                })

    ids = [record["id"] for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate held-out data cell id")
    manifest = {
        "schema_version": 1,
        "method": "heldout_source_labelled_data_cell_differential",
        "contract": {
            "reference_values": "visually source-checked before auxiliary-reader evaluation",
            "reference_crop": "Tesseract word geometry with production padding",
            "projection_crop": (
                "source-pixel row band and ink envelope inside a source-pinned "
                "structural column lane; no OCR token geometry"
            ),
            "minimum_score": MINIMUM_SCORE,
            "fallback": "reference read first; projection only after reference refusal",
            "confirmation": "retain primary only when the fallback reader agrees numerically",
            "outcomes": ["agree", "disagree", "tool_refused"],
        },
        "labels_sha256": _sha256(labels_path),
        "source_corpus_sha256": _sha256(source_corpus_path),
        "alignment_report_sha256": _sha256(alignment_dir / "report.json"),
        "documents": len({record["source_sha256"] for record in records}),
        "panels": len(labels["panels"]),
        "records": records,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for name in ("reference", "projection"):
        inputs = {
            "schema_version": 1,
            "records": [
                {
                    "id": record["id"],
                    "crop": record[f"{name}_crop"],
                    "crop_sha256": record[f"{name}_crop_sha256"],
                }
                for record in records
            ],
        }
        (output_dir / f"inputs-{name}.json").write_text(
            json.dumps(inputs, indent=2) + "\n"
        )
    return manifest


def compare(output_dir: Path, reference_run: Path, projection_run: Path) -> dict:
    manifest = json.loads((output_dir / "manifest.json").read_text())
    runs = {
        "reference": json.loads(reference_run.read_text()),
        "projection": json.loads(projection_run.read_text()),
    }
    for run in runs.values():
        _validate_reader(run.get("reader", {}))
    if runs["reference"]["reader"] != runs["projection"]["reader"]:
        raise ValueError("held-out data reader identities do not match")
    results = {
        name: {record["id"]: record for record in run["records"]}
        for name, run in runs.items()
    }
    if any(len(results[name]) != len(runs[name]["records"]) for name in runs):
        raise ValueError("duplicate held-out data reader result id")

    paths = ("primary", "tesseract", "reference", "projection", "fallback", "confirmed")
    counts = {name: Counter() for name in paths}
    panel_counts: dict[str, dict[str, Counter]] = {}
    fallback_readers = Counter()
    findings = []
    for prepared in manifest["records"]:
        for name in ("reference", "projection"):
            crop_path = output_dir / prepared[f"{name}_crop"]
            if _sha256(crop_path) != prepared[f"{name}_crop_sha256"]:
                raise ValueError(f"held-out {name} crop hash mismatch: {prepared['id']}")
        observations = {
            name: key_reader._reader_observation(
                prepared,
                results[name].get(prepared["id"]),
                projection=name == "projection",
            )
            for name in ("reference", "projection")
        }
        fallback_value, fallback_score, fallback_refusal, fallback_reader = (
            _fallback_observation(
                observations["reference"],
                observations["projection"],
                prepared["expected_kind"],
            )
        )
        primary_value = prepared["primary"]
        if (
            fallback_refusal is None
            and _semantic_value(primary_value, prepared["expected_kind"]) is not None
            and _semantic_value(primary_value, prepared["expected_kind"])
            == _semantic_value(fallback_value, prepared["expected_kind"])
        ):
            confirmed_value = primary_value
            confirmed_refusal = None
        else:
            confirmed_value = None
            confirmed_refusal = "reader_primary_disagreement"

        raw_observations = {
            "primary": (primary_value, None),
            "tesseract": (prepared["tesseract"], None),
            "reference": (observations["reference"][0], observations["reference"][2]),
            "projection": (observations["projection"][0], observations["projection"][2]),
            "fallback": (fallback_value, fallback_refusal),
            "confirmed": (confirmed_value, confirmed_refusal),
        }
        panel = panel_counts.setdefault(
            prepared["panel_id"], {name: Counter() for name in paths}
        )
        outcomes = {}
        for name, (value, refusal) in raw_observations.items():
            outcome = _outcome(
                value,
                refusal,
                prepared["expected"],
                prepared["expected_kind"],
            )
            outcomes[name] = outcome
            counts[name][outcome] += 1
            panel[name][outcome] += 1
        if fallback_reader is not None:
            fallback_readers[fallback_reader] += 1
        if any(outcome != "agree" for outcome in outcomes.values()):
            findings.append({
                "id": prepared["id"],
                "expected": prepared["expected"],
                "expected_kind": prepared["expected_kind"],
                "class": prepared["class"],
                "primary": primary_value,
                "tesseract": prepared["tesseract"],
                "reference_text": observations["reference"][0],
                "reference_score": observations["reference"][1],
                "reference_refusal": observations["reference"][2],
                "projection_text": observations["projection"][0],
                "projection_score": observations["projection"][1],
                "projection_refusal": observations["projection"][2],
                "fallback_reader": fallback_reader,
                "fallback_text": fallback_value,
                "fallback_score": fallback_score,
                "outcomes": outcomes,
            })

    report = {
        "schema_version": 1,
        "method": manifest["method"],
        "contract": manifest["contract"],
        "manifest_sha256": _sha256(output_dir / "manifest.json"),
        "reference_run_sha256": _sha256(reference_run),
        "projection_run_sha256": _sha256(projection_run),
        "reader": runs["reference"]["reader"],
        "documents": manifest["documents"],
        "panels": manifest["panels"],
        "cells": len(manifest["records"]),
        **{name: _summary(counts[name]) for name in paths},
        "fallback_readers": dict(sorted(fallback_readers.items())),
        "panel_results": {
            panel_id: {
                name: _summary(panel_paths[name])
                for name in paths
            }
            for panel_id, panel_paths in sorted(panel_counts.items())
        },
        "findings": findings,
    }
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    prepare_parser.add_argument("--source-corpus", type=Path, default=DEFAULT_SOURCE_CORPUS)
    prepare_parser.add_argument("--alignment", type=Path, default=DEFAULT_ALIGNMENT)
    prepare_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    prepare_parser.add_argument("--tesseract", default="tesseract")
    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("--reference-run", type=Path, required=True)
    compare_parser.add_argument("--projection-run", type=Path, required=True)
    compare_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    compare_parser.add_argument("--corpus", type=Path, default=DEFAULT_READER_CORPUS)
    compare_parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if args.command == "prepare":
        manifest = prepare(
            args.labels,
            args.source_corpus,
            args.alignment,
            args.output,
            args.tesseract,
        )
        print(
            f"held-out data reader: prepared {len(manifest['records'])} cells from "
            f"{manifest['panels']} panels"
        )
    else:
        report = compare(args.output, args.reference_run, args.projection_run)
        print(
            f"held-out data reader: primary {report['primary']['agree']}/"
            f"{report['cells']}, reference {report['reference']['agree']}/"
            f"{report['cells']}, projection {report['projection']['agree']}/"
            f"{report['cells']}, confirmed {report['confirmed']['agree']}/"
            f"{report['cells']}"
        )
        if args.check and not check_corpus(ROOT, args.corpus, report):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
