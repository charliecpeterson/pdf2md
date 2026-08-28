"""Prepare and score source-pinned recognition-only OCR benchmarks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import unicodedata
from collections import Counter
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps


_ROOT = Path(__file__).parent.parent
_LABELS = _ROOT / "tests" / "line_reader_labels.json"
_SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).translate(_SUBSCRIPTS)
    normalized = normalized.translate(str.maketrans({"−": "-", "–": "-", "—": "-"}))
    return re.sub(r"\s+", "", normalized.casefold())


def _line_crop(image: Image.Image, box: list[int]) -> Image.Image:
    crop = ImageOps.autocontrast(ImageOps.grayscale(image.crop(tuple(box))))
    crop = crop.resize((crop.width * 4, crop.height * 4), Image.Resampling.LANCZOS)
    crop = crop.filter(ImageFilter.UnsharpMask(radius=1, percent=100, threshold=3))
    width = max(640, crop.width)
    height = max(192, crop.height)
    canvas = Image.new("RGB", (width, height), "white")
    canvas.paste(crop, ((width - crop.width) // 2, (height - crop.height) // 2))
    crop.close()
    return canvas


def prepare(root: Path, labels: dict, output_dir: Path) -> dict:
    if labels.get("schema_version") != 1:
        raise ValueError("unsupported line reader label schema_version")
    crop_dir = output_dir / "crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    records = []
    documents = []

    for document in labels["documents"]:
        source = root / document["source"]
        if _sha256(source) != document["source_sha256"]:
            raise ValueError(f"source hash mismatch: {source}")
        document_records = []
        if "records_from" in document:
            report_path = root / document["records_from"]
            report = json.loads(report_path.read_text())
            for record in report["records"]:
                if record.get("source_row") is None:
                    continue
                source_crop = (
                    root / document["crop_dir"] /
                    f"{record['variant']}_r{record['source_row']}.png"
                )
                if not source_crop.is_file():
                    continue
                sample_id = (
                    f"{document['id']}:{record['variant']}:{record['position']:02d}"
                )
                crop_path = crop_dir / f"{sample_id.replace(':', '__')}.png"
                shutil.copyfile(source_crop, crop_path)
                document_records.append({
                    "id": sample_id,
                    "page": record["page"],
                    "condition": record["variant"],
                    "expected": record["expected"],
                    "primary": record["primary"],
                    "crop": crop_path.relative_to(output_dir).as_posix(),
                    "crop_sha256": _sha256(crop_path),
                })
        else:
            source_crop = root / document["source_crop"]
            source_crop_sha256 = _sha256(source_crop)
            with Image.open(source_crop) as image:
                for sample in document["samples"]:
                    sample_id = f"{document['id']}:{sample['id']}"
                    crop_path = crop_dir / f"{sample_id.replace(':', '__')}.png"
                    crop = _line_crop(image, sample["box"])
                    crop.save(crop_path)
                    crop.close()
                    document_records.append({
                        "id": sample_id,
                        "page": document["page"],
                        "condition": "source_scan",
                        "expected": sample["expected"],
                        "primary": sample.get("primary"),
                        "source_crop": document["source_crop"],
                        "source_crop_sha256": source_crop_sha256,
                        "box": sample["box"],
                        "crop": crop_path.relative_to(output_dir).as_posix(),
                        "crop_sha256": _sha256(crop_path),
                    })
        records.extend(document_records)
        documents.append({
            key: document[key]
            for key in ("id", "source", "source_sha256", "typeface")
        } | {
            "role": document.get("role", "development"),
            "samples": len(document_records),
        })
        for record in document_records:
            record["role"] = document.get("role", "development")

    prepared = {
        "schema_version": 1,
        "minimum_score": labels["minimum_score"],
        "documents": documents,
        "records": records,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "labels.json").write_text(json.dumps(prepared, indent=2) + "\n")
    inputs = {
        "schema_version": 1,
        "records": [
            {key: record[key] for key in ("id", "crop", "crop_sha256")}
            for record in records
        ],
    }
    (output_dir / "inputs.json").write_text(json.dumps(inputs, indent=2) + "\n")
    return prepared


def _classify(label: dict, result: dict | None, minimum_score: float) -> dict:
    reader_refusal = None
    actual = None
    score = None
    if result is None:
        reader_refusal = "result_missing"
    elif result.get("input_sha256") != label["crop_sha256"]:
        reader_refusal = "input_hash_mismatch"
    elif result.get("error"):
        reader_refusal = "reader_error"
    elif not str(result.get("text", "")).strip():
        reader_refusal = "text_missing"
    elif result.get("score") is None:
        reader_refusal = "score_missing"
    else:
        actual = str(result["text"]).strip()
        score = float(result["score"])
        if score < minimum_score:
            reader_refusal = "score_below_threshold"
    reader_outcome = (
        "tool_refused" if reader_refusal else
        "agree" if _key(actual or "") == _key(label["expected"]) else
        "disagree"
    )

    confirmation_refusal = reader_refusal
    if confirmation_refusal is None and label.get("primary") is None:
        confirmation_refusal = "primary_missing"
    if (
        confirmation_refusal is None
        and _key(actual or "") != _key(str(label["primary"]))
    ):
        confirmation_refusal = "reader_primary_disagreement"
    confirmation_outcome = (
        "tool_refused" if confirmation_refusal else
        "agree" if _key(actual or "") == _key(label["expected"]) else
        "disagree"
    )
    return {
        "actual": actual,
        "score": score,
        "reader_outcome": reader_outcome,
        "reader_refusal_reason": reader_refusal,
        "confirmation_outcome": confirmation_outcome,
        "confirmation_refusal_reason": confirmation_refusal,
    }


def _outcome_counts(records: list[dict], field: str) -> dict[str, int]:
    counts = Counter(record[field] for record in records)
    return {
        "agree": counts["agree"],
        "disagree": counts["disagree"],
        "tool_refused": counts["tool_refused"],
    }


def evaluate(labels: dict, run: dict) -> dict:
    results = {record["id"]: record for record in run.get("records", [])}
    minimum_score = labels["minimum_score"]
    records = [
        label | _classify(label, results.get(label["id"]), minimum_score)
        for label in labels["records"]
    ]

    reader_counts = _outcome_counts(records, "reader_outcome")
    confirmation_counts = _outcome_counts(records, "confirmation_outcome")
    by_role = {}
    for role in sorted({record.get("role", "development") for record in records}):
        selected = [record for record in records if record.get("role", "development") == role]
        by_role[role] = {
            "checked": len(selected),
            "reader": _outcome_counts(selected, "reader_outcome"),
            "confirmation": _outcome_counts(selected, "confirmation_outcome"),
        }
    by_document = []
    for document in labels["documents"]:
        prefix = f"{document['id']}:"
        selected = [record for record in records if record["id"].startswith(prefix)]
        by_document.append(document | {
            "reader": _outcome_counts(selected, "reader_outcome"),
            "confirmation": _outcome_counts(selected, "confirmation_outcome"),
        })
    threshold_sweep = []
    for threshold in (0.90, 0.95, 0.98, 0.99, 0.995, 0.999):
        classified = [
            label | _classify(label, results.get(label["id"]), threshold)
            for label in labels["records"]
        ]
        threshold_sweep.append({
            "minimum_score": threshold,
            "reader": _outcome_counts(classified, "reader_outcome"),
            "confirmation": _outcome_counts(classified, "confirmation_outcome"),
        })
    return {
        "schema_version": 1,
        "method": "source_pinned_recognition_only_line_ocr",
        "contract": {
            "normalization": (
                "Unicode compatibility, case, subscripts, whitespace, and minus variants; "
                "other punctuation is significant"
            ),
            "minimum_score": minimum_score,
            "outcomes": ["agree", "disagree", "tool_refused"],
            "confirmation": "reader score passes and normalized reader equals primary",
            "production_gate": "zero false confirmations across clean and degraded controls",
        },
        "reader": run.get("reader", {}),
        "checked": len(records),
        "raw_reader": reader_counts,
        "confirmation": confirmation_counts,
        "gate_passed": confirmation_counts["disagree"] == 0,
        "heldout_gate_passed": (
            by_role.get("held_out", {}).get("confirmation", {}).get("agree", 0) > 0
            and by_role.get("held_out", {}).get("confirmation", {}).get("disagree", 0) == 0
        ),
        "roles": by_role,
        "documents": by_document,
        "reader_refusal_reasons": dict(Counter(
            record["reader_refusal_reason"]
            for record in records if record["reader_refusal_reason"]
        )),
        "confirmation_refusal_reasons": dict(Counter(
            record["confirmation_refusal_reason"]
            for record in records if record["confirmation_refusal_reason"]
        )),
        "threshold_sweep": threshold_sweep,
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare or score the source-pinned line-reader benchmark."
    )
    parser.add_argument("--labels", type=Path, default=_LABELS)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit nonzero unless the declared reader gate passes.",
    )
    parser.add_argument("--check", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    prepared_path = args.output_dir / "labels.json"
    if args.run:
        prepared = json.loads(prepared_path.read_text())
        report = evaluate(prepared, json.loads(args.run.read_text()))
        report_path = args.report or args.output_dir / "report.json"
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        confirmation = report["confirmation"]
        reader = report["raw_reader"]
        print(
            f"line reader: {reader['agree']}/{report['checked']} raw reads agree; "
            f"{confirmation['agree']} confirmed, "
            f"{confirmation['disagree']} false confirmations, "
            f"{confirmation['tool_refused']} tool-refused"
        )
        if (args.strict or args.check) and not report["gate_passed"]:
            raise SystemExit(1)
    else:
        prepared = prepare(
            _ROOT,
            json.loads(args.labels.read_text()),
            args.output_dir,
        )
        print(
            f"line reader corpus: {len(prepared['records'])} crops from "
            f"{len(prepared['documents'])} documents"
        )


if __name__ == "__main__":
    main()
