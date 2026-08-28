"""Compare OCR-box and projection crops on source-checked held-out row keys."""

from __future__ import annotations

import argparse
import importlib.util
import json
from collections import Counter
from pathlib import Path

from PIL import Image

from pdf2md.line_reader import (
    MINIMUM_SCORE,
    _service_crop,
    _sha256,
    _validate_reader,
)
from pdf2md.row_locator import (
    projection_cell_box,
    projection_lane_run,
    projection_panel_bounds,
    projection_row_bands,
)
from pdf2md.table_verify import numeric_values_equal


ROOT = Path(__file__).parent.parent
DEFAULT_SOURCE_CORPUS = ROOT / "tests" / "source_row_alignment_corpus.json"
DEFAULT_READER_CORPUS = ROOT / "tests" / "heldout_key_reader_corpus.json"
DEFAULT_ALIGNMENT = ROOT / "out" / "reviews" / "source-row-alignment-heldout-v1"
DEFAULT_OUTPUT = ROOT / "out" / "reviews" / "heldout-key-reader-v1"

SPEC = importlib.util.spec_from_file_location(
    "eval_source_row_recovery", ROOT / "scripts" / "eval_source_row_recovery.py"
)
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


def _reader_observation(
    prepared: dict, result: dict | None, *, projection: bool
) -> tuple[str | None, float | None, str | None]:
    prefix = "projection_" if projection else "reference_"
    if result is None:
        return None, None, "result_missing"
    if result.get("input_sha256") != prepared[f"{prefix}crop_sha256"]:
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


def _value_outcome(value: str | None, refusal: str | None, expected: str) -> str:
    if value is None or refusal is not None:
        return "tool_refused"
    return "agree" if numeric_values_equal(value, expected) else "disagree"


def _checked_result(report: dict) -> dict:
    return {
        key: report[key]
        for key in (
            "manifest_sha256",
            "reference_run_sha256",
            "projection_run_sha256",
            "reader",
            "documents",
            "panels",
            "keys",
            "reference",
            "projection",
            "fallback",
            "fallback_readers",
            "panel_results",
            "findings",
        )
    }


def check_corpus(root: Path, corpus_path: Path, report: dict) -> bool:
    corpus = json.loads(corpus_path.read_text())
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported held-out key reader corpus schema_version")
    for name, artifact in corpus["artifacts"].items():
        if _sha256(root / artifact["path"]) != artifact["sha256"]:
            raise ValueError(f"held-out key reader artifact hash mismatch: {name}")
    return _checked_result(report) == corpus.get("expected")


def prepare(source_corpus: Path, alignment_dir: Path, output_dir: Path) -> dict:
    corpus = json.loads(source_corpus.read_text())
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported held-out alignment corpus schema_version")
    alignment = json.loads((alignment_dir / "report.json").read_text())
    if alignment.get("corpus_sha256") != _sha256(source_corpus):
        raise ValueError("held-out alignment corpus hash mismatch")
    alignment_panels = {
        panel["id"]: panel for panel in alignment["panels_report"]
    }

    reference_dir = output_dir / "reference-crops"
    projection_dir = output_dir / "projection-crops"
    reference_dir.mkdir(parents=True, exist_ok=True)
    projection_dir.mkdir(parents=True, exist_ok=True)
    records = []
    panel_reports = []
    for panel_index, panel in enumerate(corpus["panels"], start=1):
        print(f"[{panel_index}/{len(corpus['panels'])}] {panel['id']}", flush=True)
        aligned_panel = alignment_panels[panel["id"]]
        source_crop = alignment_dir / aligned_panel["source_crop"]
        tsv_path = alignment_dir / aligned_panel["tesseract_tsv"]
        if _sha256(source_crop) != panel["source_crop_sha256"]:
            raise ValueError(f"source crop hash mismatch: {panel['id']}")
        if _sha256(tsv_path) != aligned_panel["tesseract_tsv_sha256"]:
            raise ValueError(f"Tesseract TSV hash mismatch: {panel['id']}")
        tsv = tsv_path.read_text()
        lines = recovery._panel_lines(tsv, tuple(panel["key_bounds"]))
        if len(lines) != len(panel["keys"]):
            raise ValueError(f"source key line count mismatch: {panel['id']}")

        with Image.open(source_crop) as image:
            count = int(panel.get("projection_panel_count", 1))
            index = int(panel.get("projection_panel_index", 0))
            panel_bounds, _, panel_refusal = projection_panel_bounds(image, count)
            if panel_bounds is None:
                raise ValueError(
                    f"projection panel refused {panel['id']}: {panel_refusal}"
                )
            bands, _, row_refusal = projection_row_bands(
                image,
                len(panel["keys"]),
                panel_index=index,
                panel_count=count,
                stripe_fraction=float(panel["projection_stripe_fraction"]),
                panel_bounds=panel_bounds,
            )
            if bands is None:
                raise ValueError(
                    f"projection rows refused {panel['id']}: {row_refusal}"
                )

            for position, (expected, line, band) in enumerate(
                zip(panel["keys"], lines, bands)
            ):
                key_words = line[1]
                reference_crop, reference_box = _service_crop(image, key_words)
                stem = f"{panel['id']}_r{position}"
                reference_path = reference_dir / f"{stem}.png"
                reference_crop.save(reference_path)
                reference_crop.close()

                run = projection_lane_run(image, band, panel["key_bounds"])
                if run is None:
                    raise ValueError(f"projection key lane empty: {panel['id']}:{position}")
                projection_box = projection_cell_box(run, band, image.size)
                projection_crop = recovery._cell_crop(image, tuple(projection_box))
                projection_path = projection_dir / f"{stem}.png"
                projection_crop.save(projection_path)
                projection_crop.close()
                records.append({
                    "id": f"{panel['id']}:r{position}",
                    "panel_id": panel["id"],
                    "source": panel["source"],
                    "source_sha256": panel["source_sha256"],
                    "page": panel["page"],
                    "block_id": panel["block_id"],
                    "position": position,
                    "expected": expected,
                    "source_crop": str(source_crop.resolve()),
                    "source_crop_sha256": panel["source_crop_sha256"],
                    "reference_box": reference_box,
                    "reference_crop": reference_path.relative_to(output_dir).as_posix(),
                    "reference_crop_sha256": _sha256(reference_path),
                    "projection_row_band": list(band),
                    "projection_x_run": list(run),
                    "projection_box": projection_box,
                    "projection_crop": projection_path.relative_to(output_dir).as_posix(),
                    "projection_crop_sha256": _sha256(projection_path),
                })
        panel_reports.append({
            "id": panel["id"],
            "source_sha256": panel["source_sha256"],
            "page": panel["page"],
            "keys": len(panel["keys"]),
        })

    manifest = {
        "schema_version": 1,
        "method": "heldout_source_checked_key_reader_differential",
        "contract": {
            "reference_values": (
                "106 row keys source-checked before the reader experiment"
            ),
            "reference_crop": "Tesseract word geometry with production padding",
            "projection_crop": (
                "source-pixel row band and ink envelope inside the pre-established "
                "key lane; no OCR token geometry"
            ),
            "minimum_score": MINIMUM_SCORE,
            "outcomes": ["agree", "disagree", "tool_refused"],
        },
        "source_corpus": str(source_corpus.resolve()),
        "source_corpus_sha256": _sha256(source_corpus),
        "alignment_report_sha256": _sha256(alignment_dir / "report.json"),
        "documents": len({record["source_sha256"] for record in records}),
        "panels": panel_reports,
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
        raise ValueError("held-out reader identities do not match")
    results = {
        name: {record["id"]: record for record in run["records"]}
        for name, run in runs.items()
    }
    if any(len(results[name]) != len(runs[name]["records"]) for name in runs):
        raise ValueError("duplicate held-out reader result id")

    counts = {name: Counter() for name in ("reference", "projection", "fallback")}
    fallback_readers = Counter()
    findings = []
    panel_counts: dict[str, dict[str, Counter]] = {}
    for prepared in manifest["records"]:
        for name in ("reference", "projection"):
            crop_path = output_dir / prepared[f"{name}_crop"]
            if _sha256(crop_path) != prepared[f"{name}_crop_sha256"]:
                raise ValueError(f"held-out {name} crop hash mismatch: {prepared['id']}")
        observations = {}
        for name in ("reference", "projection"):
            observation = _reader_observation(
                prepared, results[name].get(prepared["id"]), projection=name == "projection"
            )
            observations[name] = observation
            outcome = _value_outcome(*observation[::2], prepared["expected"])
            counts[name][outcome] += 1
            panel_counts.setdefault(
                prepared["panel_id"],
                {
                    "reference": Counter(),
                    "projection": Counter(),
                    "fallback": Counter(),
                },
            )[name][outcome] += 1

        reference_value, _, reference_refusal = observations["reference"]
        projection_value, _, projection_refusal = observations["projection"]
        if (
            reference_refusal is None
            and numeric_values_equal(reference_value, prepared["expected"])
        ):
            fallback_outcome = "agree"
            fallback_reader = "reference"
        elif (
            projection_refusal is None
            and numeric_values_equal(projection_value, prepared["expected"])
        ):
            fallback_outcome = "agree"
            fallback_reader = "projection"
        else:
            fallback_outcome = "tool_refused"
            fallback_reader = None
        counts["fallback"][fallback_outcome] += 1
        panel_counts[prepared["panel_id"]]["fallback"][fallback_outcome] += 1
        if fallback_reader is not None:
            fallback_readers[fallback_reader] += 1
        if any(
            _value_outcome(*observations[name][::2], prepared["expected"]) != "agree"
            for name in ("reference", "projection")
        ):
            findings.append({
                "id": prepared["id"],
                "expected": prepared["expected"],
                "reference_text": reference_value,
                "reference_score": observations["reference"][1],
                "reference_refusal": reference_refusal,
                "projection_text": projection_value,
                "projection_score": observations["projection"][1],
                "projection_refusal": projection_refusal,
                "fallback_reader": fallback_reader,
            })

    def summary(counter: Counter) -> dict:
        return {
            "checked": sum(counter.values()),
            "agree": counter["agree"],
            "disagree": counter["disagree"],
            "tool_refused": counter["tool_refused"],
        }

    report = {
        "schema_version": 1,
        "method": manifest["method"],
        "contract": manifest["contract"],
        "manifest_sha256": _sha256(output_dir / "manifest.json"),
        "reference_run_sha256": _sha256(reference_run),
        "projection_run_sha256": _sha256(projection_run),
        "reader": runs["reference"]["reader"],
        "documents": manifest["documents"],
        "panels": len(manifest["panels"]),
        "keys": len(manifest["records"]),
        "reference": summary(counts["reference"]),
        "projection": summary(counts["projection"]),
        "fallback": summary(counts["fallback"]),
        "fallback_readers": dict(sorted(fallback_readers.items())),
        "panel_results": {
            panel_id: {
                name: summary(path_counts[name])
                for name in ("reference", "projection", "fallback")
            }
            for panel_id, path_counts in sorted(panel_counts.items())
        },
        "findings": findings,
    }
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--source-corpus", type=Path, default=DEFAULT_SOURCE_CORPUS)
    prepare_parser.add_argument("--alignment", type=Path, default=DEFAULT_ALIGNMENT)
    prepare_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("--reference-run", type=Path, required=True)
    compare_parser.add_argument("--projection-run", type=Path, required=True)
    compare_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    compare_parser.add_argument("--corpus", type=Path, default=DEFAULT_READER_CORPUS)
    compare_parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if args.command == "prepare":
        manifest = prepare(args.source_corpus, args.alignment, args.output)
        print(
            f"held-out key reader: prepared {len(manifest['records'])} keys from "
            f"{len(manifest['panels'])} panels"
        )
    else:
        report = compare(args.output, args.reference_run, args.projection_run)
        print(
            f"held-out key reader: reference {report['reference']['agree']}/"
            f"{report['keys']}, projection {report['projection']['agree']}/"
            f"{report['keys']}, fallback {report['fallback']['agree']}/"
            f"{report['keys']}"
        )
        if args.check and not check_corpus(ROOT, args.corpus, report):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
