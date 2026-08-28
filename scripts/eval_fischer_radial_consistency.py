"""Recompute Fischer orbital norms and moments from extracted radial tables.

The gate joins independently printed Tables I and II. It detects inconsistent or
unusable orbital grids but never supplies replacement cell values.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).parent.parent
DEFAULT_VERSION = ROOT / "out" / "0685e8d85e2237d8" / "v6"
DEFAULT_CASES = ROOT / "tests" / "atsp_fischer_reference_cases.json"
DEFAULT_CORPUS = ROOT / "tests" / "fischer_radial_consistency_corpus.json"
DEFAULT_REPORT = ROOT / "out" / "reviews" / "fischer-radial-consistency-v2.json"
NORM_TOLERANCE = 0.05
MOMENT_RELATIVE_TOLERANCE = 0.10
MOMENT_POWERS = {"1/R": -1, "R": 1, "R**2": 2}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _expected_orbitals(case: dict) -> list[str]:
    open_orbital = re.match(r"(\d[spdfg])", case["open_configuration"])
    if open_orbital is None:
        raise ValueError(f"invalid open configuration: {case['open_configuration']}")
    return [*case["closed_shells"], open_orbital.group(1)]


def _table_rows(version_dir: Path) -> list[dict[str, str]]:
    rows = []
    for path in sorted((version_dir / "data" / "tables").glob("page_*_panels.csv")):
        with path.open(newline="") as stream:
            rows.extend(csv.DictReader(stream))
    return rows


def _normalization_refusals(
    version_dir: Path, expected: set[tuple[int, str]]
) -> dict[tuple[int, str], list[str]]:
    issues: dict[tuple[int, str], list[str]] = defaultdict(list)
    for path in sorted((version_dir / "data" / "tables").glob("page_*_panels.json")):
        dataset = json.loads(path.read_text())
        if dataset.get("representation") != "repeated_panels":
            continue
        for panel in dataset.get("panels") or []:
            metadata = panel.get("metadata") or {}
            atomic_number = metadata.get("atomic_number")
            refusals = panel.get("refused_rows") or []
            if not isinstance(atomic_number, int) or not refusals:
                continue
            reasons = sorted({
                f"normalized_{refusal['reason']}"
                for refusal in refusals
            })
            for column in (panel.get("columns") or [])[1:]:
                key = (atomic_number, str(column).lower())
                if key in expected:
                    issues[key].extend(reasons)
    return issues


def _table_i_moments(rows: list[dict[str, str]]) -> dict[tuple[int, str, str], float]:
    moments = {}
    for row in rows:
        try:
            atomic_number = int(row.get("atomic_number") or "")
        except ValueError:
            continue
        row_key = (row.get("row_key") or "").lower()
        column = row.get("column") or ""
        value = row.get("numeric_value") or ""
        if not re.fullmatch(r"\d[spdfg]", row_key) or column not in MOMENT_POWERS or not value:
            continue
        key = (atomic_number, row_key, column)
        if key in moments and moments[key] != float(value):
            raise ValueError(f"conflicting Table I moment key: {key}")
        moments[key] = float(value)
    return moments


def _radial_groups(
    rows: list[dict[str, str]], expected: set[tuple[int, str]]
) -> tuple[dict[tuple[int, str], dict[float, float]], dict[tuple[int, str], list[str]]]:
    groups: dict[tuple[int, str], dict[float, float]] = defaultdict(dict)
    issues: dict[tuple[int, str], list[str]] = defaultdict(list)
    for row in rows:
        try:
            atomic_number = int(row.get("atomic_number") or "")
            radius = float(row.get("row_key") or "")
        except ValueError:
            continue
        orbital = (row.get("column") or "").lower()
        key = (atomic_number, orbital)
        if key not in expected:
            continue
        if radius < 0:
            issues[key].append("negative_radius")
            continue
        status = row.get("value_status")
        if status == "numeric":
            value = float(row["numeric_value"])
        elif status in {"dot_placeholder", "dash_placeholder"}:
            value = 0.0
        else:
            issues[key].append(f"unusable_{status or 'missing_status'}")
            continue
        if radius in groups[key] and groups[key][radius] != value:
            issues[key].append("conflicting_radius")
        groups[key][radius] = value
    return groups, issues


def _trapezoid(points: list[tuple[float, float]], power: int) -> float:
    total = 0.0
    for (left_r, left_p), (right_r, right_p) in zip(points, points[1:]):
        left = 0.0 if left_r == 0 and power < 0 else left_p * left_p * left_r**power
        right = right_p * right_p * right_r**power
        total += (right_r - left_r) * (left + right) / 2
    return total


def _relative_delta(actual: float, expected: float) -> float:
    return abs(actual - expected) / max(abs(actual), abs(expected), 1e-15)


def _checked_result(report: dict) -> dict:
    return {
        key: report[key]
        for key in (
            "source_sha256",
            "cases_sha256",
            "orbitals",
            "outcomes",
            "by_atom",
            "maximum_supported_norm_error",
            "maximum_supported_moment_relative_delta",
        )
    }


def evaluate(version_dir: Path, cases_path: Path) -> dict:
    cases = json.loads(cases_path.read_text())
    provenance = json.loads((version_dir / "provenance.json").read_text())
    if provenance.get("source_sha256") != cases["source_document_sha256"]:
        raise ValueError("Fischer source hash differs from radial-consistency cases")
    expected = {
        (case["atomic_number"], orbital.lower())
        for case in cases["cases"]
        for orbital in _expected_orbitals(case)
    }
    rows = _table_rows(version_dir)
    moments = _table_i_moments(rows)
    groups, issues = _radial_groups(rows, expected)
    for key, refusal_reasons in _normalization_refusals(version_dir, expected).items():
        issues[key].extend(refusal_reasons)
    records = []
    for atomic_number, orbital in sorted(expected):
        problems = sorted(set(issues[atomic_number, orbital]))
        points = sorted(groups[atomic_number, orbital].items())
        missing_moments = [
            column
            for column in MOMENT_POWERS
            if (atomic_number, orbital, column) not in moments
        ]
        if not points:
            problems.append("missing_radial_grid")
        if missing_moments:
            problems.append("missing_table_i_moment")
        if problems:
            records.append({
                "atomic_number": atomic_number,
                "orbital": orbital,
                "samples": len(points),
                "outcome": "tool_refused",
                "issues": sorted(set(problems)),
                "normalization": None,
                "norm_error": None,
                "moments": {},
            })
            continue

        if points[0][0] > 0:
            points.insert(0, (0.0, 0.0))
        normalization = _trapezoid(points, 0)
        comparisons = {}
        for column, power in MOMENT_POWERS.items():
            calculated = _trapezoid(points, power)
            printed = moments[atomic_number, orbital, column]
            comparisons[column] = {
                "calculated": calculated,
                "printed": printed,
                "relative_delta": _relative_delta(calculated, printed),
            }
        supported = (
            abs(normalization - 1) <= NORM_TOLERANCE
            and all(
                comparison["relative_delta"] <= MOMENT_RELATIVE_TOLERANCE
                for comparison in comparisons.values()
            )
        )
        records.append({
            "atomic_number": atomic_number,
            "orbital": orbital,
            "samples": len(points) - (points[0][0] == 0),
            "outcome": "scientifically_consistent" if supported else "disagree",
            "issues": [],
            "normalization": normalization,
            "norm_error": abs(normalization - 1),
            "moments": comparisons,
        })

    counts = Counter(record["outcome"] for record in records)
    supported = [record for record in records if record["outcome"] == "scientifically_consistent"]
    return {
        "schema_version": 1,
        "method": "radial_normalization_and_cross_table_moments",
        "contract": {
            "normalization": f"abs(integral(P^2 dr) - 1) <= {NORM_TOLERANCE:g}",
            "moments": f"trapezoid moments agree with Table I within {MOMENT_RELATIVE_TOLERANCE:g} relative",
            "placeholders": "dot and dash placeholders are treated as printed zero",
            "authority": "scientific_support_only",
            "limitation": "printed radial grids are rounded and sparse; this gate detects gross inconsistency, not last-digit accuracy",
        },
        "source_sha256": cases["source_document_sha256"],
        "cases_sha256": _sha256(cases_path),
        "orbitals": len(records),
        "outcomes": {
            "scientifically_consistent": counts["scientifically_consistent"],
            "disagree": counts["disagree"],
            "tool_refused": counts["tool_refused"],
        },
        "by_atom": {
            str(atomic_number): dict(Counter(
                record["outcome"]
                for record in records
                if record["atomic_number"] == atomic_number
            ))
            for atomic_number in sorted({record["atomic_number"] for record in records})
        },
        "maximum_supported_norm_error": max(record["norm_error"] for record in supported),
        "maximum_supported_moment_relative_delta": max(
            comparison["relative_delta"]
            for record in supported
            for comparison in record["moments"].values()
        ),
        "records": records,
    }


def check_corpus(root: Path, corpus_path: Path, report: dict) -> bool:
    corpus = json.loads(corpus_path.read_text())
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported Fischer radial corpus schema_version")
    for name, artifact in corpus["artifacts"].items():
        if _sha256(root / artifact["path"]) != artifact["sha256"]:
            raise ValueError(f"Fischer radial artifact hash mismatch: {name}")
    return _checked_result(report) == corpus["expected"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version-dir", type=Path, default=DEFAULT_VERSION)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    report = evaluate(args.version_dir, args.cases)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"Fischer radial consistency: {report['outcomes']['scientifically_consistent']}/"
        f"{report['orbitals']} supported, {report['outcomes']['disagree']} disagree, "
        f"{report['outcomes']['tool_refused']} tool-refused"
    )
    if args.check and not check_corpus(ROOT, args.corpus, report):
        raise SystemExit("Fischer radial corpus differs from expected results")


if __name__ == "__main__":
    main()
