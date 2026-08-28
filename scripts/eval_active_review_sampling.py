"""Compare active, random, and confidence-stratified numeric-cell review.

The active rank uses only evidence available before source review. Labels score each
prefix after selection and never supply category-specific error rates or priorities.
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

from pdf2md.table_review import _CONFIDENCE_ORDER, _take_diverse
from pdf2md.table_verify import typed_value


ROOT = Path(__file__).parent.parent
DEFAULT_SOURCES = ROOT / "tests" / "active_review_sampling_sources.json"
DEFAULT_CORPUS = ROOT / "tests" / "active_review_sampling_corpus.json"
DEFAULT_REPORT = ROOT / "out" / "reviews" / "active-review-sampling-v1.json"
BUDGETS = (10, 20, 40, 80, 120, 200)
RANDOM_TRIALS = 1000


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_records(root: Path, sources_path: Path) -> tuple[list[dict], dict]:
    sources = json.loads(sources_path.read_text())
    if sources.get("schema_version") != 1:
        raise ValueError("unsupported active-review sources schema_version")
    artifact = sources["artifacts"]["numeric_confidence_report"]
    path = root / artifact["path"]
    if _sha256(path) != artifact["sha256"]:
        raise ValueError("active-review numeric-confidence report hash mismatch")
    report = json.loads(path.read_text())
    records = report["records"]["natural"]
    if len({record["id"] for record in records}) != len(records):
        raise ValueError("active-review sampling frame contains duplicate cell IDs")
    return records, sources


def _is_numeric(value: object) -> bool:
    return typed_value(str(value or ""))[2] == "numeric"


def _risk_components(record: dict) -> dict[str, int]:
    confidence = {
        "not_applicable": 9,
        None: 4,
        "low": 3,
        "medium": 1,
        "high": 0,
    }.get(record.get("confidence"), 2)
    reader = {
        "disagree": 6,
        "tool_refused": 2,
        "agree": 0,
    }.get(record.get("reader_outcome"), 0)
    geometry = 4 if record.get("reader_geometry") == "refused" else 0
    refusal = {
        "grid_alignment_failed": 5,
        "cell_alignment_missing": 2,
    }.get(record.get("reader_refusal"), 0)

    basis = str(record.get("resolution_basis") or "")
    if basis == "text":
        resolver = 4
    elif basis in {"reader_not_numeric", "unresolved_primary_retained"}:
        resolver = 2
    elif basis.startswith(("column_decimal_format", "local_continuity", "numeric_type")):
        resolver = 2
    else:
        resolver = 0
    primary_syntax = 0 if _is_numeric(record.get("primary_value")) else 3
    reader_syntax = (
        3
        if record.get("reader_outcome") == "disagree"
        and not _is_numeric(record.get("reader_value"))
        else 0
    )
    return {
        "confidence": confidence,
        "reader_outcome": reader,
        "geometry": geometry,
        "refusal_reason": refusal,
        "resolver_conflict": resolver,
        "primary_syntax": primary_syntax,
        "reader_syntax": reader_syntax,
    }


def _active_order(records: list[dict], per_table: int = 3) -> list[dict]:
    family_counts = Counter(record["table_family"] for record in records)
    typography_counts = Counter(record["typography"] for record in records)
    remaining = list(records)
    selected = []
    seen_documents: set[str] = set()
    seen_families: set[str] = set()
    seen_typographies: set[str] = set()
    seen_tables: set[tuple[str, str]] = set()
    table_counts: Counter[tuple[str, str]] = Counter()
    while remaining:
        under_cap = [
            record
            for record in remaining
            if table_counts[record["source_sha256"], record["block_id"]] < per_table
        ]
        pool = under_cap or remaining

        def priority(record: dict) -> tuple[float, str]:
            base = sum(_risk_components(record).values())
            table = (record["source_sha256"], record["block_id"])
            novelty = (
                2.0 * (record["source_sha256"] not in seen_documents)
                + 1.5 * (record["typography"] not in seen_typographies)
                + 1.0 * (record["table_family"] not in seen_families)
                + 0.5 * (table not in seen_tables)
            )
            rarity = (
                1 / family_counts[record["table_family"]]
                + 1 / typography_counts[record["typography"]]
            )
            return base + novelty + rarity, record["id"]

        chosen = max(pool, key=priority)
        selection_score = priority(chosen)[0]
        remaining.remove(chosen)
        selected.append({**chosen, "_selection_score": selection_score})
        document = chosen["source_sha256"]
        table = (document, chosen["block_id"])
        seen_documents.add(document)
        seen_families.add(chosen["table_family"])
        seen_typographies.add(chosen["typography"])
        seen_tables.add(table)
        table_counts[table] += 1
    return selected


def _stratified_order(
    records: list[dict], seed: int, per_table: int = 3, limit: int | None = None
) -> list[dict]:
    proxies = [
        {
            **record,
            "source_block_id": record["block_id"],
            "source_row": record["row"],
            "source_column": record["column"],
        }
        for record in records
    ]
    strata: dict[str, list[dict]] = defaultdict(list)
    for record in proxies:
        strata[str(record.get("confidence") or "unknown")].append(record)
    names = sorted(strata, key=lambda name: (_CONFIDENCE_ORDER.get(name, 99), name))
    selected: list[dict] = []
    rng = random.Random(seed)
    while names and (limit is None or len(selected) < limit):
        progressed = False
        for name in list(names):
            if limit is not None and len(selected) >= limit:
                break
            candidate = _take_diverse(strata[name], selected, per_table, rng)
            if candidate is None:
                names.remove(name)
                continue
            selected.append(candidate)
            strata[name].remove(candidate)
            progressed = True
        if not progressed:
            break
    return selected


def _errors(records: list[dict]) -> int:
    return sum(record["primary_outcome"] == "disagree" for record in records)


def _prefix_curve(order: list[dict]) -> list[dict]:
    return [
        {
            "budget": budget,
            "errors_found": _errors(order[:budget]),
            "error_documents_found": len({
                record["source_sha256"]
                for record in order[:budget]
                if record["primary_outcome"] == "disagree"
            }),
            "errors_per_100_reviews": round(100 * _errors(order[:budget]) / budget, 4),
        }
        for budget in BUDGETS
    ]


def _percentile(values: list[int], probability: float) -> int:
    return sorted(values)[max(0, math.ceil(probability * len(values)) - 1)]


def _baseline_summary(orders: list[list[dict]]) -> list[dict]:
    summary = []
    for budget in BUDGETS:
        found = [_errors(order[:budget]) for order in orders]
        summary.append({
            "budget": budget,
            "trials": len(found),
            "mean_errors_found": round(statistics.mean(found), 4),
            "median_errors_found": statistics.median(found),
            "p10_errors_found": _percentile(found, 0.10),
            "p90_errors_found": _percentile(found, 0.90),
            "minimum_errors_found": min(found),
            "maximum_errors_found": max(found),
        })
    return summary


def _checked_result(report: dict) -> dict:
    ranking_bytes = json.dumps(
        report["active"]["ranking"], sort_keys=True, separators=(",", ":")
    ).encode()
    return {
        "sources_sha256": report["sources_sha256"],
        "sampling_frame": report["sampling_frame"],
        "signal_coverage": report["signal_coverage"],
        "active": {
            "curve": report["active"]["curve"],
            "ranking_sha256": hashlib.sha256(ranking_bytes).hexdigest(),
        },
        "random": report["random"],
        "confidence_stratified": report["confidence_stratified"],
        "conclusion": report["conclusion"],
    }


def evaluate(root: Path, sources_path: Path) -> dict:
    records, _ = _load_records(root, sources_path)
    labelled_errors = _errors(records)
    active_order = _active_order(records)
    random_orders = []
    stratified_orders = []
    for seed in range(RANDOM_TRIALS):
        random_order = list(records)
        random.Random(seed).shuffle(random_order)
        random_orders.append(random_order[:max(BUDGETS)])
        stratified_orders.append(_stratified_order(records, seed, limit=max(BUDGETS)))

    components = [_risk_components(record) for record in records]
    active_curve = _prefix_curve(active_order)
    random_summary = _baseline_summary(random_orders)
    stratified_summary = _baseline_summary(stratified_orders)
    active_at_40 = next(point for point in active_curve if point["budget"] == 40)
    random_at_40 = next(point for point in random_summary if point["budget"] == 40)
    stratified_at_40 = next(
        point for point in stratified_summary if point["budget"] == 40
    )
    active_at_120 = next(point for point in active_curve if point["budget"] == 120)
    random_at_120 = next(point for point in random_summary if point["budget"] == 120)
    stratified_at_120 = next(
        point for point in stratified_summary if point["budget"] == 120
    )
    return {
        "schema_version": 1,
        "method": "label_blind_active_review_sampling",
        "contract": {
            "ranking": "uses only pre-review evidence and category novelty; no labels, case roles, or category-specific error rates",
            "sampling_frame": "error-enriched and coverage-oriented labelled corpus; not a prevalence estimate",
            "baselines": f"uniform random and current confidence-stratified policy over seeds 0..{RANDOM_TRIALS - 1}",
            "per_table": "active and stratified policies initially cap each source table at three cells, then fill",
        },
        "sources_sha256": _sha256(sources_path),
        "sampling_frame": {
            "cells": len(records),
            "labelled_errors": labelled_errors,
            "documents": len({record["source_sha256"] for record in records}),
            "table_families": len({record["table_family"] for record in records}),
            "typographies": len({record["typography"] for record in records}),
            "role_used_for_ranking": False,
            "outcome_used_for_ranking": False,
            "prevalence_claim_allowed": False,
        },
        "signal_coverage": {
            "reader_disagreement": sum(
                record["reader_outcome"] == "disagree" for record in records
            ),
            "reader_refusal": sum(
                record["reader_outcome"] == "tool_refused" for record in records
            ),
            "geometry_refusal": sum(
                record["reader_geometry"] == "refused" for record in records
            ),
            "malformed_primary_syntax": sum(
                component["primary_syntax"] > 0 for component in components
            ),
            "malformed_disagreeing_reader_syntax": sum(
                component["reader_syntax"] > 0 for component in components
            ),
            "resolver_or_validator_conflict": sum(
                component["resolver_conflict"] > 0 for component in components
            ),
            "crop_path_instability": {"available": 0, "status": "unavailable_on_natural_error_frame"},
            "preprocessing_instability": {"available": 0, "status": "unavailable_on_natural_error_frame"},
            "typography_and_family_novelty": {
                "typographies": len({record["typography"] for record in records}),
                "table_families": len({record["table_family"] for record in records}),
                "status": "used_without_labelled_error_rates",
            },
        },
        "active": {
            "curve": active_curve,
            "ranking": [
                {
                    "rank": rank,
                    "id": record["id"],
                    "selection_score": round(record["_selection_score"], 8),
                    "risk_components": _risk_components(record),
                    "source_sha256": record["source_sha256"],
                    "block_id": record["block_id"],
                    "table_family": record["table_family"],
                    "typography": record["typography"],
                    "primary_outcome": record["primary_outcome"],
                }
                for rank, record in enumerate(active_order, start=1)
            ],
        },
        "random": {"curve": random_summary},
        "confidence_stratified": {"curve": stratified_summary},
        "conclusion": {
            "small_budget": 40,
            "small_budget_active_errors_found": active_at_40["errors_found"],
            "small_budget_random_mean_errors_found": random_at_40["mean_errors_found"],
            "small_budget_confidence_stratified_mean_errors_found": stratified_at_40["mean_errors_found"],
            "small_budget_active_error_recall": round(
                active_at_40["errors_found"] / labelled_errors, 8
            ),
            "reference_budget": 120,
            "reference_budget_active_errors_found": active_at_120["errors_found"],
            "reference_budget_random_mean_errors_found": random_at_120["mean_errors_found"],
            "reference_budget_confidence_stratified_mean_errors_found": stratified_at_120["mean_errors_found"],
            "reference_budget_active_error_recall": round(
                active_at_120["errors_found"] / labelled_errors, 8
            ),
            "promotion": "retain_confidence_stratified_default_pending_heldout_evaluation",
            "interpretation": "active ranking is a strong small-budget candidate, but its weights were evaluated on the same error-enriched corpus that motivated the signals",
            "limitation": "crop-path and preprocessing-instability signals require a future shared natural-cell frame",
        },
    }


def check_corpus(root: Path, corpus_path: Path, report: dict) -> bool:
    corpus = json.loads(corpus_path.read_text())
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported active-review corpus schema_version")
    for name, artifact in corpus["artifacts"].items():
        if _sha256(root / artifact["path"]) != artifact["sha256"]:
            raise ValueError(f"active-review artifact hash mismatch: {name}")
    return _checked_result(report) == corpus["expected"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = evaluate(args.root, args.sources)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    conclusion = report["conclusion"]
    print(
        f"active review @ {conclusion['small_budget']}: "
        f"{conclusion['small_budget_active_errors_found']} errors; random mean "
        f"{conclusion['small_budget_random_mean_errors_found']}; "
        f"confidence-stratified mean "
        f"{conclusion['small_budget_confidence_stratified_mean_errors_found']}"
    )
    if args.check and not check_corpus(args.root, args.corpus, report):
        raise SystemExit("active-review corpus differs from expected results")


if __name__ == "__main__":
    main()
