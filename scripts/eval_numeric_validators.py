"""Falsify numeric consistency rules on clean adversarial and OCR-error cases.

Each rule is scored both as a candidate preference and as a proposed primary rewrite.
Identifier and placeholder semantics remain exact rather than numeric-normalized.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from pdf2md import table_resolution
from pdf2md.table_verify import numeric_values_equal, typed_value


ROOT = Path(__file__).parent.parent
DEFAULT_CASES = ROOT / "tests" / "numeric_validator_adversarial_cases.json"
DEFAULT_CORPUS = ROOT / "tests" / "numeric_validator_adversarial_corpus.json"
DEFAULT_REPORT = ROOT / "out" / "reviews" / "numeric-validator-adversarial-v1.json"
RULES = ("numeric_type", "continuity", "decimal_format", "combined")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _correct(value: str, expected: str, kind: str) -> bool:
    if kind == "numeric":
        return numeric_values_equal(value, expected)
    if kind == "identifier":
        return value == expected
    if kind == "placeholder":
        return typed_value(value)[2] == "dot_placeholder" and value == expected
    raise ValueError(f"unsupported adversarial expected kind: {kind}")


def _numeric_type_choice(primary: str, reader: str) -> str | None:
    primary_number = table_resolution._number(primary)
    reader_number = table_resolution._number(reader)
    if primary_number is not None and reader_number is None:
        return "primary"
    return None


def _continuity_choice(case: dict) -> str | None:
    primary = table_resolution._number(case["primary"])
    reader = table_resolution._number(case["reader"])
    if primary is None or reader is None:
        return None
    row = int(case["row"])
    column = int(case["column"])
    primary_score = table_resolution._continuity_score(
        case["rows"], row, column, 0, primary
    )
    reader_score = table_resolution._continuity_score(
        case["rows"], row, column, 0, reader
    )
    if primary_score is None or reader_score is None:
        return None
    if primary_score <= 2 and reader_score >= max(4, primary_score * 5):
        return "primary"
    if reader_score <= 2 and primary_score >= max(4, reader_score * 5):
        return "reader"
    return None


def _decimal_choice(case: dict) -> str | None:
    if (
        table_resolution._number(case["primary"]) is None
        or table_resolution._number(case["reader"]) is None
    ):
        return None
    expected_places = table_resolution._dominant_decimal_places(
        case["rows"], int(case["column"])
    )
    if expected_places is None:
        return None
    primary_matches = (
        table_resolution._decimal_places(case["primary"]) == expected_places
    )
    reader_matches = (
        table_resolution._decimal_places(case["reader"]) == expected_places
    )
    if primary_matches == reader_matches:
        return None
    return "primary" if primary_matches else "reader"


def _combined_choice(case: dict) -> tuple[str | None, str | None]:
    decision = table_resolution._resolve_disagreement(
        {
            "source_row": case["row"],
            "source_column": case["column"],
        },
        case["rows"],
        None,
        case["primary"],
        case["reader"],
    )
    return decision if decision is not None else (None, None)


def _rule_summary(records: list[dict], rule: str) -> dict:
    outcomes = Counter(record["rules"][rule]["outcome"] for record in records)
    detection = Counter(record["rules"][rule]["detection"] for record in records)
    rewrites = Counter(record["rules"][rule]["rewrite"] for record in records)
    return {
        "checked": len(records),
        "correct_preference": outcomes["agree"],
        "wrong_preference": outcomes["disagree"],
        "tool_refused": outcomes["tool_refused"],
        "primary_error_detection": {
            key: detection[key]
            for key in ("true_positive", "false_positive", "false_negative", "true_negative")
        },
        "proposed_rewrite": {
            key: rewrites[key]
            for key in ("correction", "regression", "retained", "unresolved")
        },
    }


def _checked_result(report: dict) -> dict:
    checked = {
        key: report[key]
        for key in (
            "cases_sha256",
            "cases",
            "adversarial_cases",
            "control_cases",
            "rules",
            "numeric_equivalence_false_agreements",
            "placeholder_type_checks",
        )
    }
    checked["records_sha256"] = _json_sha256(report["records"])
    return checked


def evaluate(cases_path: Path) -> dict:
    source = json.loads(cases_path.read_text())
    if source.get("schema_version") != 1:
        raise ValueError("unsupported numeric validator cases schema_version")
    records = []
    for case in source["cases"]:
        primary_correct = _correct(
            case["primary"], case["expected"], case["expected_kind"]
        )
        reader_correct = _correct(
            case["reader"], case["expected"], case["expected_kind"]
        )
        if primary_correct == reader_correct:
            raise ValueError(f"case must have exactly one correct candidate: {case['id']}")
        choices = {
            "numeric_type": (_numeric_type_choice(case["primary"], case["reader"]), None),
            "continuity": (_continuity_choice(case), None),
            "decimal_format": (_decimal_choice(case), None),
            "combined": _combined_choice(case),
        }
        rule_records = {}
        for rule, (choice, basis) in choices.items():
            if choice is None:
                outcome = "tool_refused"
            else:
                chosen = case[choice]
                outcome = (
                    "agree"
                    if _correct(chosen, case["expected"], case["expected_kind"])
                    else "disagree"
                )
            if choice == "reader":
                detection = "true_positive" if not primary_correct else "false_positive"
                rewrite = "correction" if reader_correct else "regression"
            elif not primary_correct:
                detection = "false_negative"
                rewrite = "retained" if choice == "primary" else "unresolved"
            else:
                detection = "true_negative"
                rewrite = "retained" if choice == "primary" else "unresolved"
            rule_records[rule] = {
                "choice": choice,
                "basis": basis,
                "outcome": outcome,
                "detection": detection,
                "rewrite": rewrite,
            }
        false_numeric_agreement = (
            numeric_values_equal(case["primary"], case["reader"])
            and case["primary"] != case["reader"]
            and not reader_correct
        )
        records.append({
            "id": case["id"],
            "class": case["class"],
            "expected_kind": case["expected_kind"],
            "primary_correct": primary_correct,
            "reader_correct": reader_correct,
            "primary_type": typed_value(case["primary"])[2],
            "reader_type": typed_value(case["reader"])[2],
            "numeric_equivalence_false_agreement": false_numeric_agreement,
            "rules": rule_records,
        })

    adversarial = [record for record in records if record["primary_correct"]]
    controls = [record for record in records if not record["primary_correct"]]
    classes = sorted({record["class"] for record in records})
    report = {
        "schema_version": 1,
        "method": "numeric_validator_adversarial_falsification",
        "contract": {
            "ground_truth": "candidate correctness is fixed explicitly per clean synthetic table",
            "detector": "reader preference flags the primary as suspect",
            "resolver": "reader preference is scored as a proposed primary rewrite",
            "semantics": "identifiers and placeholders use exact typed equality, not numeric normalization",
        },
        "cases_sha256": _sha256(cases_path),
        "cases": len(records),
        "adversarial_cases": len(adversarial),
        "control_cases": len(controls),
        "rules": {rule: _rule_summary(records, rule) for rule in RULES},
        "numeric_equivalence_false_agreements": sum(
            record["numeric_equivalence_false_agreement"] for record in records
        ),
        "placeholder_type_checks": {
            "checked": sum(record["expected_kind"] == "placeholder" for record in records),
            "correct": sum(
                record["expected_kind"] == "placeholder"
                and record["primary_type"] == "dot_placeholder"
                and record["reader_type"] == "numeric"
                for record in records
            ),
        },
        "by_class": {
            class_name: {
                rule: _rule_summary(
                    [record for record in records if record["class"] == class_name],
                    rule,
                )
                for rule in RULES
            }
            for class_name in classes
        },
        "records": records,
    }
    return report


def check_corpus(root: Path, corpus_path: Path, report: dict) -> bool:
    corpus = json.loads(corpus_path.read_text())
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported numeric validator corpus schema_version")
    for name, artifact in corpus["artifacts"].items():
        if _sha256(root / artifact["path"]) != artifact["sha256"]:
            raise ValueError(f"numeric validator artifact hash mismatch: {name}")
    return _checked_result(report) == corpus["expected"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    report = evaluate(args.cases)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    combined = report["rules"]["combined"]
    print(
        f"numeric validators: {combined['correct_preference']} correct preferences, "
        f"{combined['wrong_preference']} wrong, {combined['tool_refused']} refused; "
        f"{combined['proposed_rewrite']['correction']} corrections, "
        f"{combined['proposed_rewrite']['regression']} regressions"
    )
    if args.check and not check_corpus(ROOT, args.corpus, report):
        raise SystemExit("numeric validator corpus differs from expected results")


if __name__ == "__main__":
    main()
