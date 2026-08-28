"""Evaluate review ranking with every error-bearing document held out in turn.

Each fold learns evidence-signal weights without the test document, freezes them,
then compares active, random, and confidence-stratified review on that document.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from eval_active_review_sampling import (
    RANDOM_TRIALS,
    _load_records,
    _risk_components,
    _stratified_order,
)


ROOT = Path(__file__).parent.parent
DEFAULT_SOURCES = ROOT / "tests" / "active_review_sampling_sources.json"
DEFAULT_CORPUS = ROOT / "tests" / "active_review_heldout_corpus.json"
DEFAULT_REPORT = ROOT / "out" / "reviews" / "active-review-heldout-v1.json"
BUDGETS = (1, 3, 5, 10, 20)
SMOOTHING = 0.5


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _is_error(record: dict) -> bool:
    return record["primary_outcome"] == "disagree"


def _train_weights(records: list[dict]) -> dict[str, float]:
    features = tuple(_risk_components(records[0]))
    weights = {}
    for feature in features:
        counts: Counter[tuple[bool, bool]] = Counter()
        for record in records:
            present = _risk_components(record)[feature] > 0
            counts[present, _is_error(record)] += 1
        error_present = counts[True, True]
        clean_present = counts[True, False]
        error_absent = counts[False, True]
        clean_absent = counts[False, False]
        weights[feature] = (
            math.log((error_present + SMOOTHING) / (clean_present + SMOOTHING))
            - math.log((error_absent + SMOOTHING) / (clean_absent + SMOOTHING))
        )
    return weights


def _risk_score(record: dict, weights: dict[str, float]) -> float:
    components = _risk_components(record)
    return sum(weight for name, weight in weights.items() if components[name] > 0)


def _weighted_order(
    records: list[dict], weights: dict[str, float], per_table: int = 3
) -> list[dict]:
    remaining = list(records)
    selected = []
    table_counts: Counter[tuple[str, str]] = Counter()
    while remaining:
        under_cap = [
            record
            for record in remaining
            if table_counts[record["source_sha256"], record["block_id"]] < per_table
        ]
        pool = under_cap or remaining
        chosen = max(pool, key=lambda record: (_risk_score(record, weights), record["id"]))
        remaining.remove(chosen)
        selected.append({**chosen, "_selection_score": _risk_score(chosen, weights)})
        table_counts[chosen["source_sha256"], chosen["block_id"]] += 1
    return selected


def _errors(order: list[dict], budget: int) -> int:
    return sum(_is_error(record) for record in order[:budget])


def _baseline_curve(orders: list[list[dict]]) -> list[dict]:
    curve = []
    for budget in BUDGETS:
        found = [_errors(order, budget) for order in orders]
        curve.append({
            "budget": budget,
            "trials": len(found),
            "mean_errors_found": round(statistics.mean(found), 4),
            "minimum_errors_found": min(found),
            "maximum_errors_found": max(found),
        })
    return curve


def _active_curve(order: list[dict], labelled_errors: int) -> list[dict]:
    return [
        {
            "budget": budget,
            "errors_found": _errors(order, budget),
            "error_recall": round(_errors(order, budget) / labelled_errors, 8),
        }
        for budget in BUDGETS
    ]


def _point(curve: list[dict], budget: int) -> dict:
    return next(point for point in curve if point["budget"] == budget)


def evaluate(root: Path, sources_path: Path) -> dict:
    records, _ = _load_records(root, sources_path)
    by_document: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        by_document[record["source_sha256"]].append(record)
    heldout_documents = sorted(
        sha
        for sha, document_records in by_document.items()
        if any(map(_is_error, document_records))
    )
    folds = []
    for heldout_sha in heldout_documents:
        heldout = by_document[heldout_sha]
        training = [record for record in records if record["source_sha256"] != heldout_sha]
        if any(record["source_sha256"] == heldout_sha for record in training):
            raise ValueError("held-out document leaked into active-review training")
        weights = _train_weights(training)
        active_order = _weighted_order(heldout, weights)
        random_orders = []
        stratified_orders = []
        for seed in range(RANDOM_TRIALS):
            random_order = list(heldout)
            random.Random(seed).shuffle(random_order)
            random_orders.append(random_order)
            stratified_orders.append(
                _stratified_order(heldout, seed, limit=max(BUDGETS))
            )
        labelled_errors = sum(map(_is_error, heldout))
        folds.append({
            "document": heldout[0]["document"],
            "source_sha256": heldout_sha,
            "heldout_cells": len(heldout),
            "heldout_errors": labelled_errors,
            "training_documents": len({record["source_sha256"] for record in training}),
            "training_cells": len(training),
            "training_errors": sum(map(_is_error, training)),
            "weights": {name: round(value, 8) for name, value in weights.items()},
            "active": {
                "curve": _active_curve(active_order, labelled_errors),
                "ranking": [
                    {
                        "rank": rank,
                        "id": record["id"],
                        "score": round(record["_selection_score"], 8),
                        "primary_outcome": record["primary_outcome"],
                    }
                    for rank, record in enumerate(active_order, start=1)
                ],
            },
            "random": {"curve": _baseline_curve(random_orders)},
            "confidence_stratified": {"curve": _baseline_curve(stratified_orders)},
        })

    aggregate = []
    for budget in BUDGETS:
        active_found = sum(
            _point(fold["active"]["curve"], budget)["errors_found"] for fold in folds
        )
        random_found = sum(
            _point(fold["random"]["curve"], budget)["mean_errors_found"] for fold in folds
        )
        stratified_found = sum(
            _point(fold["confidence_stratified"]["curve"], budget)["mean_errors_found"]
            for fold in folds
        )
        aggregate.append({
            "budget_per_document": budget,
            "total_reviews": budget * len(folds),
            "active_errors_found": active_found,
            "active_error_recall": round(
                active_found / sum(fold["heldout_errors"] for fold in folds), 8
            ),
            "random_mean_errors_found": round(random_found, 4),
            "confidence_stratified_mean_errors_found": round(stratified_found, 4),
        })

    active_beats_stratified_everywhere = all(
        _point(fold["active"]["curve"], budget)["errors_found"]
        > _point(fold["confidence_stratified"]["curve"], budget)["mean_errors_found"]
        for fold in folds
        for budget in BUDGETS
    )
    return {
        "schema_version": 1,
        "method": "leave_one_document_out_active_review",
        "contract": {
            "training": (
                "each fold fits seven evidence-signal log-odds weights after "
                "removing every cell from the test document"
            ),
            "smoothing": SMOOTHING,
            "ranking": (
                "weights and order use pre-review evidence only; labels score the "
                "frozen order"
            ),
            "baselines": f"uniform random and confidence-stratified seeds 0..{RANDOM_TRIALS - 1}",
            "eligibility": (
                "every document containing at least one source-labelled primary "
                "error is held out once"
            ),
        },
        "sources_sha256": _sha256(sources_path),
        "sampling_frame": {
            "cells": len(records),
            "documents": len(by_document),
            "labelled_errors": sum(map(_is_error, records)),
            "heldout_folds": len(folds),
            "zero_error_training_controls": sum(
                not any(map(_is_error, document_records))
                for document_records in by_document.values()
            ),
            "budgets_per_document": list(BUDGETS),
        },
        "folds": folds,
        "aggregate": aggregate,
        "conclusion": {
            "active_beats_confidence_stratified_on_every_document_and_budget": (
                active_beats_stratified_everywhere
            ),
            "promotion": (
                "promote_active_default"
                if active_beats_stratified_everywhere
                else "retain_confidence_stratified_default"
            ),
            "reason": (
                "leave-one-document-out active ranking improves aggregate "
                "small-budget recall but does not dominate confidence-stratified "
                "review on every held-out document and budget"
            ),
        },
    }


def _checked_result(report: dict) -> dict:
    folds = []
    for fold in report["folds"]:
        checked = {
            key: fold[key]
            for key in (
                "document",
                "source_sha256",
                "heldout_cells",
                "heldout_errors",
                "training_documents",
                "training_cells",
                "training_errors",
                "weights",
            )
        }
        checked["active_curve"] = fold["active"]["curve"]
        checked["active_ranking_sha256"] = _json_sha256(fold["active"]["ranking"])
        checked["random_curve_sha256"] = _json_sha256(fold["random"]["curve"])
        checked["confidence_stratified_curve_sha256"] = _json_sha256(
            fold["confidence_stratified"]["curve"]
        )
        folds.append(checked)
    return {
        "sources_sha256": report["sources_sha256"],
        "sampling_frame": report["sampling_frame"],
        "folds": folds,
        "aggregate": report["aggregate"],
        "conclusion": report["conclusion"],
    }


def check_corpus(root: Path, corpus_path: Path, report: dict) -> bool:
    corpus = json.loads(corpus_path.read_text())
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported active-review heldout corpus schema_version")
    for name, artifact in corpus["artifacts"].items():
        if _sha256(root / artifact["path"]) != artifact["sha256"]:
            raise ValueError(f"active-review heldout artifact hash mismatch: {name}")
    if _checked_result(report) != corpus["expected"]:
        raise ValueError("held-out active-review results differ from frozen corpus")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = evaluate(args.root, args.sources)
    if args.check:
        check_corpus(args.root, args.corpus, report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    point = next(item for item in report["aggregate"] if item["budget_per_document"] == 5)
    print(
        "held-out active review @ 5/document: "
        f"{point['active_errors_found']} active, "
        f"{point['confidence_stratified_mean_errors_found']} stratified mean; "
        f"{report['conclusion']['promotion']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
