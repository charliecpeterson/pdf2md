"""Evaluate selective third-reader adjudication on source-labelled table cells.

The primary and first independent reader trigger a third read only when both values
exist and disagree. Gold labels score the cascade but never select a candidate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from eval_numeric_tables import _values_equal


def _key(record: dict[str, object]) -> tuple[str, str, int, int]:
    return (
        str(record["source_sha256"]),
        str(record["block_id"]),
        int(record["row"]),
        int(record["column"]),
    )


def _validator_preference(record: dict[str, object]) -> str | None:
    basis = str(record.get("resolution_basis") or "")
    if basis in {
        "reader_not_numeric",
        "local_continuity_primary",
        "column_decimal_format_primary",
    }:
        return "primary"
    if basis in {"local_continuity_reader", "column_decimal_format_reader"}:
        return "reader"
    return None


def _outcome(value: str | None, expected: str) -> str:
    if value is None:
        return "tool_refused"
    return "agree" if _values_equal(value, expected) else "disagree"


def evaluate(primary_report: dict, adjudicator_report: dict) -> dict:
    adjudicator = {_key(record): record for record in adjudicator_report["records"]}
    records = []
    for source in primary_report["records"]:
        expected = str(source["expected"])
        primary = source.get("actual")
        reader = source.get("reference_actual")
        selected = str(primary) if primary is not None else None
        status = "primary_missing" if primary is None else "reader_refused"
        paddle = None
        validator = _validator_preference(source)

        triggered = (
            primary is not None
            and reader is not None
            and not _values_equal(str(primary), str(reader))
        )
        if primary is not None and reader is not None and not triggered:
            status = "reader_agreement"
        elif triggered:
            paddle_record = adjudicator.get(_key(source))
            paddle = paddle_record.get("reference_actual") if paddle_record else None
            if paddle is None:
                status = "adjudicator_refused"
            else:
                matches_primary = _values_equal(str(paddle), str(primary))
                matches_reader = _values_equal(str(paddle), str(reader))
                if matches_primary == matches_reader:
                    status = "adjudicator_third_value"
                else:
                    majority = "primary" if matches_primary else "reader"
                    if validator is not None and validator != majority:
                        status = "validator_rejected"
                    else:
                        status = f"majority_{majority}"
                        if majority == "reader":
                            selected = str(reader)

        baseline_outcome = _outcome(
            str(primary) if primary is not None else None, expected
        )
        cascade_outcome = _outcome(selected, expected)
        records.append({
            "source_sha256": source["source_sha256"],
            "page": source["page"],
            "block_id": source["block_id"],
            "row": source["row"],
            "column": source["column"],
            "expected": expected,
            "primary": primary,
            "reader": reader,
            "adjudicator": paddle,
            "triggered": triggered,
            "validator_preference": validator,
            "cascade_status": status,
            "selected": selected,
            "baseline_outcome": baseline_outcome,
            "outcome": cascade_outcome,
        })

    counts = {
        name: sum(record["outcome"] == name for record in records)
        for name in ("agree", "disagree", "tool_refused")
    }
    baseline = {
        name: sum(record["baseline_outcome"] == name for record in records)
        for name in ("agree", "disagree", "tool_refused")
    }
    statuses = {
        name: sum(record["cascade_status"] == name for record in records)
        for name in (
            "reader_agreement",
            "reader_refused",
            "primary_missing",
            "majority_primary",
            "majority_reader",
            "adjudicator_refused",
            "adjudicator_third_value",
            "validator_rejected",
        )
    }
    return {
        "schema_version": 1,
        "method": "selective_three_reader_cascade",
        "contract": {
            "trigger": "primary_reader_disagreement",
            "decision": "third_reader_matches_exactly_one_candidate",
            "validator": "existing_format_or_continuity_rule_must_not_oppose_majority",
            "fallback": "retain_primary_unresolved",
        },
        "checked": len(records),
        **counts,
        "baseline": baseline,
        "triggered": sum(record["triggered"] for record in records),
        "avoided_adjudicator_calls": sum(not record["triggered"] for record in records),
        "statuses": statuses,
        "corrections": sum(
            record["baseline_outcome"] == "disagree" and record["outcome"] == "agree"
            for record in records
        ),
        "regressions": sum(
            record["baseline_outcome"] == "agree" and record["outcome"] == "disagree"
            for record in records
        ),
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate selective third-reader table-cell adjudication."
    )
    parser.add_argument("primary_report", type=Path)
    parser.add_argument("adjudicator_report", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit nonzero on any disagreement or refusal.",
    )
    parser.add_argument("--check", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    report = evaluate(
        json.loads(args.primary_report.read_text()),
        json.loads(args.adjudicator_report.read_text()),
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"cascade: {report['agree']}/{report['checked']} agree, "
        f"{report['disagree']} disagree, {report['tool_refused']} tool-refused; "
        f"{report['triggered']} third-reader calls, {report['corrections']} corrections, "
        f"{report['regressions']} regressions"
    )
    if (args.strict or args.check) and (
        report["disagree"] or report["tool_refused"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
