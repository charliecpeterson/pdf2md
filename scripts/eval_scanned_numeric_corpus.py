"""Evaluate source-checked numeric cells across scanned-document output roots.

The manifest separates known primary errors from clean controls. Expected counts
pin a measured conversion baseline without treating intentional error cases as a pass.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from eval_numeric_tables import evaluate as evaluate_labels


_MANIFEST = Path(__file__).parent.parent / "tests" / "scanned_numeric_corpus.json"
_COUNT_KEYS = ("checked", "agree", "disagree", "tool_refused")


def evaluate(root: Path, manifest: dict) -> dict:
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported scanned numeric corpus schema_version")

    cases = []
    totals = {key: 0 for key in _COUNT_KEYS}
    for case in manifest.get("cases", []):
        labels_path = root / case["labels"]
        labels = json.loads(labels_path.read_text())
        primary = evaluate_labels(root / case["out_dir"], labels)
        counts = {key: primary[key] for key in _COUNT_KEYS}
        expected = case.get("expected_primary")
        baseline_matches = expected is None or counts == expected
        documents = [
            {
                "source": document["source"],
                "source_sha256": document["source_sha256"],
                "version": document.get("version"),
            }
            for document in labels.get("documents", [])
        ]
        cases.append({
            "id": case["id"],
            "role": case["role"],
            "labels": case["labels"],
            "out_dir": case["out_dir"],
            "documents": documents,
            "primary": primary,
            "expected_primary": expected,
            "baseline_matches": baseline_matches,
        })
        for key in _COUNT_KEYS:
            totals[key] += counts[key]

    roles = {}
    for case in cases:
        role_counts = roles.setdefault(
            case["role"], {key: 0 for key in _COUNT_KEYS}
        )
        for key in _COUNT_KEYS:
            role_counts[key] += case["primary"][key]

    return {
        "schema_version": 1,
        "method": "multi_document_scanned_numeric_cell_evaluation",
        "contract": {
            "labels": "values read from source pixels and pinned by source SHA-256",
            "outcomes": ["agree", "disagree", "tool_refused"],
            "known_errors": "expected disagreements are retained as correction tests",
        },
        **totals,
        "roles": roles,
        "baseline_matches": all(case["baseline_matches"] for case in cases),
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate the multi-document scanned numeric-table corpus."
    )
    parser.add_argument("--manifest", type=Path, default=_MANIFEST)
    parser.add_argument("--root", type=Path, default=Path(__file__).parent.parent)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    report = evaluate(args.root, json.loads(args.manifest.read_text()))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"scanned numeric corpus: {report['agree']}/{report['checked']} agree, "
        f"{report['disagree']} disagree, {report['tool_refused']} tool-refused"
    )
    for case in report["cases"]:
        primary = case["primary"]
        print(
            f"  {case['id']}: {primary['agree']}/{primary['checked']} agree, "
            f"{primary['disagree']} disagree, {primary['tool_refused']} tool-refused"
        )
    if args.check and not report["baseline_matches"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
