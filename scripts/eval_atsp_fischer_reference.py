"""Compare Fischer Table I with term-specific ATSP Hartree-Fock calculations.

The calculation inputs define atomic number, term, configuration, and orbital keys
without reading them from OCR. Numerical proximity is scientific support, not an
exact external replacement for the historical printed value.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).parent.parent
DEFAULT_VERSION = ROOT / "out" / "0685e8d85e2237d8" / "v6"
DEFAULT_CASES = ROOT / "tests" / "atsp_fischer_reference_cases.json"
DEFAULT_RUN = ROOT / "out" / "reviews" / "atsp-fischer-v1"
DEFAULT_CORPUS = ROOT / "tests" / "atsp_fischer_reference_corpus.json"
DEFAULT_REPORT = ROOT / "out" / "reviews" / "atsp-fischer-v2" / "report.json"
RELATIVE_TOLERANCE = 1e-4


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_hf_log(path: Path) -> dict[tuple[str, str], str]:
    values: dict[tuple[str, str], str] = {}
    section = None
    for line in path.read_text().splitlines():
        if "nl       E(nl)" in line:
            section = "orbital"
            continue
        if "nl      Delta(R)" in line:
            section = "radial"
            continue
        if "TOTAL ENERGY (a.u.)" in line:
            section = "energy"
            continue
        match = re.match(r"^\s+(\d[spdfg])\s+(.+)$", line)
        if match and section in {"orbital", "radial"}:
            orbital = match.group(1)
            fields = match.group(2).split()
            if section == "orbital" and len(fields) == 6:
                values[(orbital, "E")] = fields[0]
                values[(orbital, "S")] = fields[4]
                values[(orbital, "A")] = fields[5]
            elif section == "radial" and len(fields) == 5:
                values[(orbital, "1/R**3")] = fields[1]
                values[(orbital, "1/R")] = fields[2]
                values[(orbital, "R")] = fields[3]
                values[(orbital, "R**2")] = fields[4]
            continue
        if section == "energy" and "Non-Relativistic" in line:
            energy = re.search(r"Non-Relativistic\s+([-+0-9.]+)", line)
            if energy:
                values[("TOTAL ENERGY =", "value")] = energy.group(1)
    if ("TOTAL ENERGY =", "value") not in values:
        raise ValueError(f"ATSP log has no completed total energy: {path}")
    return values


def _load_extracted(version_dir: Path) -> dict[tuple[int, str, str], str]:
    values = {}
    for path in sorted((version_dir / "data" / "tables").glob("page_*_panels.csv")):
        with path.open(newline="") as stream:
            for row in csv.DictReader(stream):
                if not row.get("atomic_number"):
                    continue
                row_key = row["row_key"].lower()
                if not re.fullmatch(r"\d[spdfg]", row_key) and row_key != "total energy =":
                    continue
                key = (int(row["atomic_number"]), row_key, row["column"])
                value = row.get("numeric_value") or ""
                if key in values and value and values[key] and value != values[key]:
                    raise ValueError(f"conflicting extracted semantic key: {key}")
                if value or key not in values:
                    values[key] = value
    return values


def _relative_delta(actual: float, expected: float) -> float:
    return abs(actual - expected) / max(abs(actual), abs(expected), 1e-15)


def _checked_result(report: dict) -> dict:
    return {
        key: report[key]
        for key in (
            "source_sha256",
            "cases_sha256",
            "run_manifest_sha256",
            "atoms",
            "cells",
            "scientific_support",
            "exact_numeric_agreement",
            "maximum_relative_delta",
            "by_field",
        )
    }


def evaluate(version_dir: Path, cases_path: Path, run_dir: Path) -> dict:
    provenance = json.loads((version_dir / "provenance.json").read_text())
    cases = json.loads(cases_path.read_text())
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    source_hash = cases["source_document_sha256"]
    if provenance.get("source_sha256") != source_hash:
        raise ValueError("Fischer source hash differs from ATSP reference cases")
    if manifest.get("cases_sha256") != _sha256(cases_path):
        raise ValueError("ATSP run cases hash mismatch")
    case_by_id = {case["id"]: case for case in cases["cases"]}
    if set(case_by_id) != {run["id"] for run in manifest["runs"]}:
        raise ValueError("ATSP run set differs from reference cases")

    extracted = _load_extracted(version_dir)
    records = []
    for run in manifest["runs"]:
        if run["exit_code"] or not run.get("completed_result"):
            raise ValueError(f"incomplete ATSP reference run: {run['id']}")
        for field in ("input", "stdout", "hf_log"):
            path = run_dir / run[field]
            if _sha256(path) != run[f"{field}_sha256"]:
                raise ValueError(f"ATSP {field} hash mismatch: {run['id']}")
        case = case_by_id[run["id"]]
        for (row_key, column), expected_text in _parse_hf_log(
            run_dir / run["hf_log"]
        ).items():
            if column == "1/R**3" and row_key.endswith("s"):
                continue
            key = (case["atomic_number"], row_key.lower(), column)
            actual_text = extracted.get(key, "")
            if not actual_text:
                outcome = "tool_refused"
                absolute_delta = None
                relative_delta = None
            else:
                actual = float(actual_text)
                expected = float(expected_text)
                absolute_delta = abs(actual - expected)
                relative_delta = _relative_delta(actual, expected)
                outcome = (
                    "scientifically_consistent"
                    if relative_delta <= RELATIVE_TOLERANCE
                    else "disagree"
                )
            records.append({
                "case_id": run["id"],
                "atomic_number": case["atomic_number"],
                "symbol": case["symbol"],
                "term": case["term"],
                "row_key": row_key,
                "column": column,
                "atsp_value": expected_text,
                "extracted_value": actual_text or None,
                "absolute_delta": absolute_delta,
                "relative_delta": relative_delta,
                "outcome": outcome,
                "verification_status": "scientific_support",
            })

    counts = Counter(record["outcome"] for record in records)
    by_field = {}
    for column in sorted({record["column"] for record in records}):
        field_records = [record for record in records if record["column"] == column]
        field_counts = Counter(record["outcome"] for record in field_records)
        by_field[column] = {
            "checked": len(field_records),
            "scientifically_consistent": field_counts["scientifically_consistent"],
            "disagree": field_counts["disagree"],
            "tool_refused": field_counts["tool_refused"],
        }
    return {
        "schema_version": 1,
        "method": "term_specific_atsp_scientific_cross_check",
        "contract": {
            "semantic_keys": "atomic configuration inputs and ATSP orbital labels are authored independently of OCR",
            "agreement": f"same sign and relative delta at most {RELATIVE_TOLERANCE:g}",
            "authority": "scientific_support_only",
            "limitation": "later code shares Fischer method lineage and cannot replace historical printed digits",
        },
        "source_sha256": source_hash,
        "cases_sha256": _sha256(cases_path),
        "run_manifest_sha256": _sha256(manifest_path),
        "atoms": len(cases["cases"]),
        "cells": len(records),
        "scientific_support": {
            "agree": counts["scientifically_consistent"],
            "disagree": counts["disagree"],
            "tool_refused": counts["tool_refused"],
        },
        "exact_numeric_agreement": sum(
            record["extracted_value"] is not None
            and float(record["extracted_value"]) == float(record["atsp_value"])
            for record in records
        ),
        "maximum_relative_delta": max(
            record["relative_delta"]
            for record in records
            if record["relative_delta"] is not None
        ),
        "by_field": by_field,
        "records": records,
    }


def check_corpus(root: Path, corpus_path: Path, report: dict) -> bool:
    corpus = json.loads(corpus_path.read_text())
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported ATSP Fischer corpus schema_version")
    for name, artifact in corpus["artifacts"].items():
        if _sha256(root / artifact["path"]) != artifact["sha256"]:
            raise ValueError(f"ATSP Fischer artifact hash mismatch: {name}")
    return _checked_result(report) == corpus["expected"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version-dir", type=Path, default=DEFAULT_VERSION)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    report = evaluate(args.version_dir, args.cases, args.run_dir)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    support = report["scientific_support"]
    print(
        f"ATSP Fischer: {support['agree']}/{report['cells']} supported, "
        f"{support['disagree']} disagree, {support['tool_refused']} tool-refused"
    )
    if args.check and not check_corpus(ROOT, args.corpus, report):
        raise SystemExit("ATSP Fischer corpus differs from expected results")


if __name__ == "__main__":
    main()
