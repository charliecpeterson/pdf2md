"""Calibrate numeric evidence as selective prediction, never as a rewrite rule.

The report keeps each signal's labelled denominator visible. It evaluates reader
trust separately from proposed value replacement and uses held-out documents when a
continuous score supplies a threshold that could otherwise overfit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from eval_heldout_data_reader import _outcome, _semantic_value
from pdf2md.table_verify import numeric_values_equal, typed_value


ROOT = Path(__file__).parent.parent
DEFAULT_SOURCES = ROOT / "tests" / "numeric_confidence_sources.json"
DEFAULT_CORPUS = ROOT / "tests" / "numeric_confidence_corpus.json"
FIXED_THRESHOLDS = (0.0, 0.8, 0.9, 0.95, 0.99, 0.995, 0.999)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_artifacts(root: Path, sources: dict) -> dict[str, dict]:
    artifacts = {}
    for name, artifact in sources["artifacts"].items():
        path = root / artifact["path"]
        if _sha256(path) != artifact["sha256"]:
            raise ValueError(f"numeric confidence artifact hash mismatch: {name}")
        artifacts[name] = json.loads(path.read_text())
    return artifacts


def _wilson_upper(errors: int, checked: int) -> float | None:
    if not checked:
        return None
    z = 1.959963984540054
    rate = errors / checked
    denominator = 1 + z * z / checked
    center = rate + z * z / (2 * checked)
    radius = z * math.sqrt(
        rate * (1 - rate) / checked + z * z / (4 * checked * checked)
    )
    return round((center + radius) / denominator, 8)


def _primary_point(name: str, selected: list[dict], total: int) -> dict:
    errors = sum(record["primary_outcome"] == "disagree" for record in selected)
    return {
        "gate": name,
        "accepted": len(selected),
        "coverage": round(len(selected) / total, 8) if total else None,
        "errors": errors,
        "error_rate": round(errors / len(selected), 8) if selected else None,
        "error_rate_upper_95": _wilson_upper(errors, len(selected)),
    }


def _natural_signals(records: list[dict]) -> dict:
    total = len(records)
    confidence_order = ("high", "medium", "low", "not_applicable", None)
    confidence_curve = []
    admitted = set()
    for confidence in confidence_order:
        admitted.add(confidence)
        selected = [record for record in records if record["confidence"] in admitted]
        confidence_curve.append(
            _primary_point(
                "through_missing" if confidence is None else f"through_{confidence}",
                selected,
                total,
            )
        )

    readers_agree = [record for record in records if record["readers_agree"]]
    geometry_available = [
        record for record in records if record["reader_geometry"] == "available"
    ]
    validator_records = [
        record
        for record in records
        if str(record.get("resolution_basis") or "").startswith(
            ("column_decimal_format", "local_continuity", "numeric_type")
        )
    ]
    external_records = [
        record
        for record in records
        if record.get("resolution_basis") == "external_reference"
    ]
    return {
        "cells": total,
        "documents": len({record["source_sha256"] for record in records}),
        "reader_agreement": [
            _primary_point("readers_agree", readers_agree, total),
            _primary_point("all_primary_values", records, total),
        ],
        "reported_confidence": confidence_curve,
        "geometry_status": [
            _primary_point("reader_geometry_available", geometry_available, total),
            _primary_point("all_geometry_states", records, total),
        ],
        "numeric_validators": {
            "status": "insufficient_labelled_coverage",
            "curve": [_primary_point("validator_support", validator_records, total)],
        },
        "document_consistency": {
            "status": "insufficient_labelled_coverage",
            "curve": [
                _primary_point("document_consistency_support", validator_records, total)
            ],
        },
        "external_reference": {
            "status": "unavailable",
            "curve": [_primary_point("externally_verified", external_records, total)],
        },
        "reader_agreement_by_document": [
            {
                "source_sha256": source_sha256,
                **_primary_point(
                    "readers_agree",
                    [record for record in group if record["readers_agree"]],
                    len(group),
                ),
                "labelled_cells": len(group),
                "labelled_errors": sum(
                    record["primary_outcome"] == "disagree" for record in group
                ),
            }
            for source_sha256 in sorted({record["source_sha256"] for record in records})
            if (group := [
                record for record in records if record["source_sha256"] == source_sha256
            ])
        ],
    }


def _semantic_equal(left: str | None, right: str | None, kind: str) -> bool:
    left_value = _semantic_value(left, kind)
    right_value = _semantic_value(right, kind)
    return left_value is not None and left_value == right_value


def _pp_records(artifacts: dict[str, dict]) -> list[dict]:
    manifest = artifacts["heldout_manifest"]
    reference = {
        record["id"]: record for record in artifacts["heldout_reference_run"]["records"]
    }
    projection = {
        record["id"]: record for record in artifacts["heldout_projection_run"]["records"]
    }
    records = []
    for source in manifest["records"]:
        candidate = reference[source["id"]]
        alternate = projection[source["id"]]
        expected_kind = source["expected_kind"]
        candidate_outcome = _outcome(
            candidate.get("text"),
            candidate.get("error"),
            source["expected"],
            expected_kind,
        )
        records.append({
            "id": source["id"],
            "source_sha256": source["source_sha256"],
            "document": Path(source["source"]).name,
            "sample_role": "heldout_clean_control",
            "expected": source["expected"],
            "expected_kind": expected_kind,
            "primary": source["primary"],
            "primary_outcome": _outcome(
                source["primary"], None, source["expected"], expected_kind
            ),
            "first_reader": source["tesseract"],
            "first_reader_agrees_primary": _semantic_equal(
                source["tesseract"], source["primary"], expected_kind
            ),
            "candidate": candidate.get("text"),
            "candidate_score": candidate.get("score"),
            "candidate_outcome": candidate_outcome,
            "candidate_agrees_primary": _semantic_equal(
                candidate.get("text"), source["primary"], expected_kind
            ),
            "crop_paths_agree": _semantic_equal(
                candidate.get("text"), alternate.get("text"), expected_kind
            ),
            "geometry_status": "independent_projection_available",
        })

    for source in artifacts["third_reader_report"]["records"]:
        candidate = source.get("third_reader")
        records.append({
            "id": (
                f"{source['source_sha256']}:{source['block_id']}:"
                f"{source['row']}:{source['column']}"
            ),
            "source_sha256": source["source_sha256"],
            "document": source["document"],
            "sample_role": "natural_primary_error",
            "expected": source["expected"],
            "expected_kind": "numeric",
            "primary": source["primary"],
            "primary_outcome": source["baseline_outcome"],
            "first_reader": source.get("first_reader"),
            "first_reader_agrees_primary": (
                source.get("first_reader") is not None
                and numeric_values_equal(source["primary"], source["first_reader"])
            ),
            "candidate": candidate,
            "candidate_score": source.get("third_reader_score"),
            "candidate_outcome": source["raw_outcome"],
            "candidate_agrees_primary": (
                candidate is not None
                and typed_value(candidate)[2] == "numeric"
                and numeric_values_equal(candidate, source["primary"])
            ),
            "crop_paths_agree": None,
            "geometry_status": "human_verified_source_box",
        })
    if len({record["id"] for record in records}) != len(records):
        raise ValueError("numeric confidence PP-OCRv6 records are not unique")
    return records


def _score_point(threshold: float, records: list[dict], total: int) -> dict:
    accepted = [
        record
        for record in records
        if record["candidate_score"] is not None
        and float(record["candidate_score"]) >= threshold
        and record["candidate_outcome"] != "tool_refused"
    ]
    wrong = sum(record["candidate_outcome"] == "disagree" for record in accepted)
    proposals = [record for record in accepted if not record["candidate_agrees_primary"]]
    corrections = sum(
        record["primary_outcome"] == "disagree"
        and record["candidate_outcome"] == "agree"
        for record in proposals
    )
    regressions = sum(
        record["primary_outcome"] == "agree"
        and record["candidate_outcome"] == "disagree"
        for record in proposals
    )
    wrong_to_wrong = sum(
        record["primary_outcome"] == "disagree"
        and record["candidate_outcome"] == "disagree"
        for record in proposals
    )
    wrong_replacements = regressions + wrong_to_wrong
    return {
        "threshold": threshold,
        "accepted": len(accepted),
        "coverage": round(len(accepted) / total, 8) if total else None,
        "correct": len(accepted) - wrong,
        "wrong": wrong,
        "error_rate": round(wrong / len(accepted), 8) if accepted else None,
        "error_rate_upper_95": _wilson_upper(wrong, len(accepted)),
        "proposals": len(proposals),
        "corrections": corrections,
        "regressions": regressions,
        "wrong_to_wrong": wrong_to_wrong,
        "wrong_replacement_rate_upper_95": _wilson_upper(
            wrong_replacements, len(proposals)
        ),
    }


def _score_curve(records: list[dict]) -> list[dict]:
    thresholds = sorted(
        {
            float(record["candidate_score"])
            for record in records
            if record["candidate_score"] is not None
        },
        reverse=True,
    )
    return [_score_point(threshold, records, len(records)) for threshold in thresholds]


def _zero_wrong_training_threshold(records: list[dict]) -> float:
    wrong_scores = [
        float(record["candidate_score"])
        for record in records
        if record["candidate_score"] is not None
        and record["candidate_outcome"] == "disagree"
    ]
    return math.nextafter(max(wrong_scores), math.inf) if wrong_scores else 0.0


def _leave_one_document_out(records: list[dict]) -> list[dict]:
    folds = []
    for source_sha256 in sorted({record["source_sha256"] for record in records}):
        train = [record for record in records if record["source_sha256"] != source_sha256]
        test = [record for record in records if record["source_sha256"] == source_sha256]
        threshold = _zero_wrong_training_threshold(train)
        folds.append({
            "held_out_source_sha256": source_sha256,
            "held_out_document": sorted({record["document"] for record in test}),
            "train": _score_point(threshold, train, len(train)),
            "test": _score_point(threshold, test, len(test)),
            "fixed_0_99_test": _score_point(0.99, test, len(test)),
        })
    return folds


def _glyph_signals(artifacts: dict[str, dict]) -> dict:
    records = []
    for name in ("fischer_glyph_report", "slater_glyph_report", "pdf059_glyph_report"):
        for ranking in artifacts[name]["rankings"]:
            records.append({"document": name, **ranking})
    comparable = [record for record in records if record.get("score_margin") is not None]
    thresholds = sorted(
        {float(record["score_margin"]) for record in comparable}, reverse=True
    )
    curve = []
    for threshold in thresholds:
        selected = [
            record
            for record in comparable
            if float(record["score_margin"]) >= threshold
        ]
        wrong = sum(record["outcome"] == "disagree" for record in selected)
        curve.append({
            "threshold": round(threshold, 12),
            "accepted": len(selected),
            "coverage": round(len(selected) / len(records), 8),
            "correct": len(selected) - wrong,
            "wrong": wrong,
            "error_rate_upper_95": _wilson_upper(wrong, len(selected)),
        })
    stable = [record for record in comparable if record.get("jackknife_stable")]
    stable_wrong = sum(record["outcome"] == "disagree" for record in stable)
    return {
        "labelled_rankings": len(records),
        "comparable_margins": len(comparable),
        "refusals": len(records) - len(comparable),
        "margin_curve": curve,
        "jackknife_stability": {
            "available": len(stable),
            "correct": len(stable) - stable_wrong,
            "wrong": stable_wrong,
        },
    }


def _checked_result(report: dict) -> dict:
    return {
        key: report[key]
        for key in (
            "sources_sha256",
            "natural_signals",
            "pp_reader",
            "glyph_similarity",
            "signal_status",
            "promotion_gate",
        )
    }


def evaluate(root: Path, sources_path: Path) -> dict:
    sources = json.loads(sources_path.read_text())
    if sources.get("schema_version") != 1:
        raise ValueError("unsupported numeric confidence sources schema_version")
    artifacts = _load_artifacts(root, sources)
    natural_records = artifacts["natural_report"]["records"]
    pp_records = _pp_records(artifacts)
    roles = Counter(record["sample_role"] for record in pp_records)
    if roles != {"heldout_clean_control": 56, "natural_primary_error": 14}:
        raise ValueError("numeric confidence PP-OCRv6 sample composition drifted")
    if any(
        record["primary_outcome"] != "agree"
        for record in pp_records
        if record["sample_role"] == "heldout_clean_control"
    ):
        raise ValueError("held-out clean controls contain a primary error")
    if any(
        record["primary_outcome"] != "disagree"
        for record in pp_records
        if record["sample_role"] == "natural_primary_error"
    ):
        raise ValueError("natural primary-error sample contains a clean primary")
    fixed_curve = [
        _score_point(threshold, pp_records, len(pp_records))
        for threshold in FIXED_THRESHOLDS
    ]
    fixed_099 = next(point for point in fixed_curve if point["threshold"] == 0.99)
    learned_folds = _leave_one_document_out(pp_records)
    heldout_wrong = sum(fold["test"]["wrong"] for fold in learned_folds)
    glyph = _glyph_signals(artifacts)
    natural = _natural_signals(natural_records)
    report = {
        "schema_version": 1,
        "method": "selective_numeric_confidence_calibration",
        "contract": {
            "sampling": "error-enriched and coverage-oriented corpora; not a prevalence estimate",
            "selection": "every curve reports its own labelled denominator and refusals",
            "replacement": "candidate trust and proposed primary replacement are scored separately",
            "held_out": "score thresholds are selected without the held-out source hash",
        },
        "sources_sha256": _sha256(sources_path),
        "natural_signals": natural,
        "pp_reader": {
            "cells": len(pp_records),
            "documents": len({record["source_sha256"] for record in pp_records}),
            "roles": dict(sorted(roles.items())),
            "fixed_threshold_curve": fixed_curve,
            "full_score_curve": _score_curve(pp_records),
            "leave_one_document_out": learned_folds,
            "learned_threshold_heldout_wrong": heldout_wrong,
            "crop_path_agreement": {
                "eligible": sum(record["crop_paths_agree"] is not None for record in pp_records),
                "agree": sum(record["crop_paths_agree"] is True for record in pp_records),
                "disagree": sum(record["crop_paths_agree"] is False for record in pp_records),
                "natural_errors_with_two_paths": sum(
                    record["sample_role"] == "natural_primary_error"
                    and record["crop_paths_agree"] is not None
                    for record in pp_records
                ),
            },
        },
        "glyph_similarity": glyph,
        "signal_status": {
            "reader_agreement": "measured_on_2113_cells",
            "reader_score": "measured_on_70_cells_from_5_source_documents",
            "crop_path_agreement": "clean_controls_only_no_error_recall_estimate",
            "geometry_status": "measured_but_not_a_correctness_score",
            "numeric_validators": "one_labelled_supported_cell_insufficient",
            "glyph_similarity": "measured_on_17_rankings_but_two_stable_choices_are_wrong",
            "document_consistency": "one_labelled_supported_cell_insufficient",
            "external_reference": "zero_mapped_cells_unavailable",
        },
        "promotion_gate": {
            "status": "not_defined",
            "fixed_0_99": fixed_099,
            "reason": (
                "The fixed threshold has zero observed wrong reads, but only two "
                "proposed corrections and a wide wrong-replacement upper bound. "
                "A threshold learned on the other documents also admits a held-out "
                "wrong read."
            ),
        },
        "records": {
            "natural": natural_records,
            "pp_reader": pp_records,
        },
    }
    return report


def check_corpus(root: Path, corpus_path: Path, report: dict) -> bool:
    corpus = json.loads(corpus_path.read_text())
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported numeric confidence corpus schema_version")
    for name, artifact in corpus["artifacts"].items():
        if _sha256(root / artifact["path"]) != artifact["sha256"]:
            raise ValueError(f"numeric confidence corpus artifact hash mismatch: {name}")
    return _checked_result(report) == corpus["expected"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calibrate numeric evidence with selective prediction curves."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    report = evaluate(args.root, args.sources)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    fixed = report["promotion_gate"]["fixed_0_99"]
    print(
        f"numeric confidence: score 0.99 accepts {fixed['accepted']}/"
        f"{report['pp_reader']['cells']} reads, {fixed['wrong']} wrong; "
        f"{fixed['corrections']} corrections, {fixed['regressions']} regressions"
    )
    if args.check and not check_corpus(args.root, args.corpus, report):
        raise SystemExit("numeric confidence corpus differs from expected results")


if __name__ == "__main__":
    main()
