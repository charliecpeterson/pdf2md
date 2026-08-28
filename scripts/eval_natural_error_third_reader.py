"""Evaluate the pinned third reader on independently boxed natural OCR errors."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from pdf2md.line_reader import MINIMUM_SCORE, _validate_reader
from pdf2md.table_verify import numeric_values_equal, typed_value


ROOT = Path(__file__).parent.parent
DEFAULT_CORPUS = ROOT / "tests" / "non_fischer_third_reader_corpus.json"
OUTCOMES = ("agree", "disagree", "tool_refused")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _key(source_sha256: str, record: dict) -> tuple[str, str, int, int]:
    return (
        source_sha256,
        record["block_id"],
        int(record.get("source_row", record.get("row"))),
        int(record.get("source_column", record.get("column"))),
    )


def _summary(outcomes: list[str]) -> dict[str, int]:
    counts = Counter(outcomes)
    return {"checked": len(outcomes), **{name: counts[name] for name in OUTCOMES}}


def _numeric_outcome(value: str | None, expected: str) -> str:
    if value is None or typed_value(value)[2] != "numeric":
        return "tool_refused"
    return "agree" if numeric_values_equal(value, expected) else "disagree"


def _candidate_outcome(value: str | None, expected: str) -> str:
    if value is None:
        return "tool_refused"
    return "agree" if numeric_values_equal(value, expected) else "disagree"


def _source_evidence(corpus: dict, artifacts: dict[str, dict]) -> dict:
    evidence = {}
    for source_sha256, artifact_name in corpus["source_reports"].items():
        for record in artifacts[artifact_name]["records"]:
            key = _key(source_sha256, record)
            if key in evidence:
                raise ValueError(f"duplicate source evidence: {key}")
            evidence[key] = {
                "primary": record.get("actual", record.get("primary_value")),
                "reader": record.get(
                    "reference_actual", record.get("reader_value")
                ),
                "reader_refusal": record.get(
                    "reference_refusal_reason", record.get("refusal_reason")
                ),
            }
    return evidence


def _checked_result(report: dict) -> dict:
    return {
        key: report[key]
        for key in (
            "documents",
            "cells",
            "geometry",
            "raw_numeric_reader",
            "fixed_threshold_reader",
            "preserved_cascade",
            "accepted_correct_after_first_reader_refusal",
            "per_document",
        )
    }


def evaluate(root: Path, corpus: dict) -> dict:
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported natural-error third-reader schema_version")
    artifacts = {}
    artifact_paths = {}
    for name, artifact in corpus["artifacts"].items():
        path = root / artifact["path"]
        if _sha256(path) != artifact["sha256"]:
            raise ValueError(f"third-reader artifact hash mismatch: {name}")
        artifact_paths[name] = path
        if path.suffix == ".json":
            artifacts[name] = json.loads(path.read_text())

    labels = artifacts["labels"]
    manifest = artifacts["crop_manifest"]
    run = artifacts["reader_run"]
    _validate_reader(run.get("reader", {}))
    if manifest.get("method") != "human_verified_source_pixel_boxes":
        raise ValueError("third-reader crop geometry is not independent")
    if manifest.get("labels") != labels:
        raise ValueError("third-reader crop labels diverged")
    if manifest.get("source_boxes_sha256") != corpus["artifacts"]["source_boxes"]["sha256"]:
        raise ValueError("third-reader source-box manifest diverged")

    expected_by_key = {
        _key(document["source_sha256"], cell): str(cell["expected"])
        for document in labels["documents"]
        for cell in document["cells"]
    }
    document_by_source = {
        document["source_sha256"]: document.get("id", Path(document["source"]).name)
        for document in labels["documents"]
    }
    crops = {
        _key(record["source_sha256"], record): record
        for record in manifest["crops"]
    }
    if len(crops) != len(manifest["crops"]):
        raise ValueError("duplicate third-reader crop key")
    if crops.keys() != expected_by_key.keys() or manifest.get("refusals"):
        raise ValueError("third-reader geometry does not cover every labelled error")
    source_evidence = _source_evidence(corpus, artifacts)
    if not crops.keys() <= source_evidence.keys():
        raise ValueError("third-reader source evidence is incomplete")
    results = {record["id"]: record for record in run["records"]}
    if len(results) != len(run["records"]):
        raise ValueError("duplicate third-reader result id")

    records = []
    per_document_outcomes = {}
    for key, crop in crops.items():
        crop_path = artifact_paths["crop_manifest"].parent / crop["path"]
        if _sha256(crop_path) != crop["crop_sha256"]:
            raise ValueError(f"third-reader crop hash mismatch: {crop['id']}")
        result = results.get(crop["id"])
        raw_value = None
        score = None
        fixed_value = None
        refusal = None
        if result is None:
            refusal = "result_missing"
        elif result.get("input_sha256") != crop["crop_sha256"]:
            refusal = "input_hash_mismatch"
        elif result.get("error"):
            refusal = "reader_error"
        else:
            raw_value = str(result.get("text") or "").strip() or None
            score = (
                float(result["score"]) if result.get("score") is not None else None
            )
            if raw_value is None:
                refusal = "reader_text_missing"
            elif score is None:
                refusal = "reader_score_missing"
            elif score < MINIMUM_SCORE:
                refusal = "reader_score_below_threshold"
            elif typed_value(raw_value)[2] != "numeric":
                refusal = "reader_value_not_numeric"
            else:
                fixed_value = raw_value

        expected = expected_by_key[key]
        raw_outcome = _numeric_outcome(raw_value, expected)
        fixed_outcome = _numeric_outcome(fixed_value, expected)
        source = source_evidence[key]
        primary = source["primary"]
        first_reader = source["reader"]
        triggered = (
            primary is not None
            and first_reader is not None
            and not numeric_values_equal(str(primary), str(first_reader))
        )
        selected = str(primary) if primary is not None else None
        cascade_status = "reader_refused"
        if triggered:
            if fixed_value is None:
                cascade_status = "adjudicator_refused"
            else:
                matches_primary = numeric_values_equal(fixed_value, str(primary))
                matches_reader = numeric_values_equal(fixed_value, str(first_reader))
                if matches_primary == matches_reader:
                    cascade_status = "adjudicator_third_value"
                elif matches_primary:
                    cascade_status = "majority_primary"
                else:
                    cascade_status = "majority_reader"
                    selected = str(first_reader)
        baseline_outcome = _candidate_outcome(
            str(primary) if primary is not None else None, expected
        )
        cascade_outcome = _candidate_outcome(selected, expected)
        document = document_by_source[key[0]]
        document_outcomes = per_document_outcomes.setdefault(
            document, {"raw": [], "fixed": [], "cascade": []}
        )
        document_outcomes["raw"].append(raw_outcome)
        document_outcomes["fixed"].append(fixed_outcome)
        document_outcomes["cascade"].append(cascade_outcome)
        records.append({
            "source_sha256": key[0],
            "document": document,
            "page": crop["page"],
            "block_id": key[1],
            "row": key[2],
            "column": key[3],
            "expected": expected,
            "primary": primary,
            "first_reader": first_reader,
            "first_reader_refusal": source["reader_refusal"],
            "third_reader": raw_value,
            "third_reader_score": score,
            "third_reader_refusal": refusal,
            "raw_outcome": raw_outcome,
            "fixed_threshold_outcome": fixed_outcome,
            "cascade_triggered": triggered,
            "cascade_status": cascade_status,
            "cascade_selected": selected,
            "baseline_outcome": baseline_outcome,
            "cascade_outcome": cascade_outcome,
        })

    raw = _summary([record["raw_outcome"] for record in records])
    fixed = _summary([record["fixed_threshold_outcome"] for record in records])
    cascade = _summary([record["cascade_outcome"] for record in records])
    cascade.update({
        "triggered": sum(record["cascade_triggered"] for record in records),
        "corrections": sum(
            record["baseline_outcome"] == "disagree"
            and record["cascade_outcome"] == "agree"
            for record in records
        ),
        "regressions": sum(
            record["baseline_outcome"] == "agree"
            and record["cascade_outcome"] == "disagree"
            for record in records
        ),
        "statuses": dict(sorted(Counter(
            record["cascade_status"] for record in records
        ).items())),
    })
    report = {
        "schema_version": 1,
        "method": "natural_error_third_reader_independent_geometry",
        "contract": {
            "geometry": "human-verified source boxes fixed before recognition",
            "reader_gate": f"pinned PP-OCRv6 score >= {MINIMUM_SCORE}",
            "promotion": "none; accepted readings remain evaluation evidence",
            "cascade": "preserved trigger requires a non-null primary/first-reader disagreement",
        },
        "reader": run["reader"],
        "documents": len(document_by_source),
        "cells": len(records),
        "geometry": {
            "prepared": len(crops),
            "refused": len(manifest["refusals"]),
            "method": manifest["method"],
        },
        "raw_numeric_reader": raw,
        "fixed_threshold_reader": fixed,
        "preserved_cascade": cascade,
        "accepted_correct_after_first_reader_refusal": sum(
            record["first_reader"] is None
            and record["fixed_threshold_outcome"] == "agree"
            for record in records
        ),
        "per_document": [
            {
                "document": document,
                "raw_numeric_reader": _summary(outcomes["raw"]),
                "fixed_threshold_reader": _summary(outcomes["fixed"]),
                "preserved_cascade": _summary(outcomes["cascade"]),
            }
            for document, outcomes in per_document_outcomes.items()
        ],
        "records": records,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate independent-geometry third reads on natural errors."
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    corpus = json.loads(args.corpus.read_text())
    report = evaluate(args.root, corpus)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    fixed = report["fixed_threshold_reader"]
    cascade = report["preserved_cascade"]
    print(
        f"natural-error third reader: {fixed['agree']}/{fixed['checked']} accepted "
        f"correct, {fixed['disagree']} accepted wrong, {fixed['tool_refused']} refused; "
        f"cascade {cascade['corrections']} corrections from {cascade['triggered']} triggers"
    )
    if args.check and _checked_result(report) != corpus["expected"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
