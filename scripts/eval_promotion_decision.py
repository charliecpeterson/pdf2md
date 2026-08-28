"""Synthesize frozen value-promotion evidence without inventing a risk threshold.

The report records what each experiment can support and keeps exact external
authority separate from OCR, geometry, consistency, and review-ranking evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).parent.parent
DEFAULT_SOURCES = ROOT / "tests" / "promotion_decision_sources.json"
DEFAULT_CORPUS = ROOT / "tests" / "promotion_decision_corpus.json"
DEFAULT_REPORT = ROOT / "out" / "reviews" / "promotion-decision-v1.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_artifacts(root: Path, sources: dict) -> dict[str, dict]:
    artifacts = {}
    for name, artifact in sources["artifacts"].items():
        path = root / artifact["path"]
        if _sha256(path) != artifact["sha256"]:
            raise ValueError(f"promotion decision artifact hash mismatch: {name}")
        artifacts[name] = json.loads(path.read_text())
    return artifacts


def evaluate(root: Path, sources_path: Path) -> dict:
    sources = json.loads(sources_path.read_text())
    if sources.get("schema_version") != 1:
        raise ValueError("unsupported promotion decision sources schema_version")
    artifacts = _load_artifacts(root, sources)
    confidence = artifacts["numeric_confidence"]
    active = artifacts["active_review"]
    stability = artifacts["rendering_stability"]
    internal = artifacts["internal_checks"]
    geometry = artifacts["column_geometry"]
    external = artifacts["external_reference_audit"]

    fixed = confidence["promotion_gate"]["fixed_0_99"]
    external_summary = external["summary"]
    if internal["contract"]["replacement_values_emitted"]:
        raise ValueError("internal checks unexpectedly emit replacement values")
    if external_summary["candidates_with_matching_extracted_fields"]:
        raise ValueError("external audit has an overlapping source requiring review")

    return {
        "schema_version": 1,
        "method": "frozen_numeric_promotion_decision_synthesis",
        "contract": {
            "threshold": "no acceptable false-correction bound is invented here",
            "authority": (
                "only an exact independent semantic reference or human review may "
                "supply a replacement value"
            ),
            "other_signals": (
                "OCR, geometry, rendering stability, and scientific relations may "
                "rank, support, or flag cells but cannot replace them"
            ),
        },
        "sources_sha256": _sha256(sources_path),
        "evidence": {
            "fixed_reader_score_0_99": {
                key: fixed[key]
                for key in (
                    "accepted",
                    "correct",
                    "wrong",
                    "proposals",
                    "corrections",
                    "regressions",
                    "wrong_replacement_rate_upper_95",
                )
            },
            "learned_threshold_heldout_wrong": confidence["pp_reader"][
                "learned_threshold_heldout_wrong"
            ],
            "natural_external_reference_cells": confidence["natural_signals"][
                "external_reference"
            ]["curve"][0]["accepted"],
            "external_reference_audit": external_summary,
            "internal_exact_checks": {
                "checks": internal["checks"],
                "outcomes": internal["outcomes"],
                "replacement_values_emitted": internal["contract"][
                    "replacement_values_emitted"
                ],
            },
            "rendering_instability": stability["combined_instability_prediction"],
            "new_layout_consensus": geometry["methods"]["consensus"],
            "heldout_review": active["conclusion"],
        },
        "decision": {
            "automatic_ocr_value_promotion": confidence["promotion_gate"]["status"],
            "exact_external_reference_override": (
                "eligible_when_supplied_and_semantically_mapped"
            ),
            "new_external_adapter": external_summary["decision"],
            "review_default": active["conclusion"]["promotion"],
            "rendering_instability": "review_ranking_only",
            "internal_scientific_checks": "support_or_review_only",
            "experimental_column_locators": "evaluation_only",
            "next_required_evidence": (
                "more held-out proposed corrections or an independent source with "
                "overlapping semantic fields"
            ),
        },
    }


def _checked_result(report: dict) -> dict:
    evidence = report["evidence"]
    stability = evidence["rendering_instability"]
    return {
        "sources_sha256": report["sources_sha256"],
        "evidence": {
            "fixed_reader_score_0_99": evidence["fixed_reader_score_0_99"],
            "learned_threshold_heldout_wrong": evidence[
                "learned_threshold_heldout_wrong"
            ],
            "natural_external_reference_cells": evidence[
                "natural_external_reference_cells"
            ],
            "external_reference_audit": evidence["external_reference_audit"],
            "internal_exact_checks": evidence["internal_exact_checks"],
            "rendering_instability": {
                key: stability[key]
                for key in (
                    "cells",
                    "unstable_primary_errors",
                    "unstable_clean_primary",
                    "primary_error_sensitivity",
                    "primary_error_specificity",
                    "primary_error_positive_predictive_value",
                )
            },
            "new_layout_consensus": evidence["new_layout_consensus"],
            "heldout_review": {
                "active_beats_confidence_stratified_on_every_document_and_budget": (
                    evidence["heldout_review"][
                        "active_beats_confidence_stratified_on_every_document_and_budget"
                    ]
                ),
                "promotion": evidence["heldout_review"]["promotion"],
            },
        },
        "decision": report["decision"],
    }


def check_corpus(root: Path, corpus_path: Path, report: dict) -> bool:
    corpus = json.loads(corpus_path.read_text())
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported promotion decision corpus schema_version")
    for name, artifact in corpus["artifacts"].items():
        if _sha256(root / artifact["path"]) != artifact["sha256"]:
            raise ValueError(f"promotion decision corpus artifact hash mismatch: {name}")
    return _checked_result(report) == corpus["expected"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Synthesize the frozen numeric value-promotion decision."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    report = evaluate(args.root, args.sources)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    decision = report["decision"]
    print(
        "promotion decision: "
        f"OCR={decision['automatic_ocr_value_promotion']}; "
        f"external adapter={decision['new_external_adapter']}"
    )
    if args.check and not check_corpus(args.root, args.corpus, report):
        raise SystemExit("promotion decision corpus differs from expected results")


if __name__ == "__main__":
    main()
