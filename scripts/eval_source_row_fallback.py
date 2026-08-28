"""Verify that projection fallback adds candidates without changing prior ones."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from pdf2md.line_reader import _sha256
from pdf2md.table_verify import numeric_values_equal


ROOT = Path(__file__).parent.parent
DEFAULT_CORPUS = ROOT / "tests" / "source_row_fallback_corpus.json"
DEFAULT_OUTPUT = ROOT / "out" / "reviews" / "source-row-fallback-corpus-v1"


def _cells(path: Path) -> dict[str, dict]:
    cells = {}
    for line in path.read_text().splitlines():
        row = json.loads(line)
        for cell in row["cells"]:
            if cell["id"] in cells:
                raise ValueError(f"duplicate source-row cell id: {cell['id']}")
            cells[cell["id"]] = cell
    return cells


def _prior_outcome(old: dict, new: dict | None) -> str:
    if new is None or new.get("candidate_value") is None:
        return "tool_refused"
    return (
        "agree"
        if old["candidate_value"] == new["candidate_value"]
        else "disagree"
    )


def _score_adjudications(
    labels: dict, source_sha256: str, cells: dict[str, dict]
) -> dict:
    if labels.get("schema_version") != 1:
        raise ValueError("unsupported projection adjudication schema_version")
    if labels.get("source_sha256") != source_sha256:
        raise ValueError("projection adjudication source hash mismatch")
    counts = Counter()
    for label in labels["records"]:
        cell = cells.get(label["id"])
        if cell is None or cell.get("candidate_value") is None:
            counts["tool_refused"] += 1
            continue
        if cell["crop_sha256"] != label["reference_crop_sha256"]:
            raise ValueError(f"adjudication reference hash mismatch: {label['id']}")
        if cell["projection_crop_sha256"] != label["projection_crop_sha256"]:
            raise ValueError(f"adjudication projection hash mismatch: {label['id']}")
        outcome = (
            "agree"
            if numeric_values_equal(cell["candidate_value"], label["expected"])
            else "disagree"
        )
        counts[outcome] += 1
    return {
        "checked": len(labels["records"]),
        "agree": counts["agree"],
        "disagree": counts["disagree"],
        "tool_refused": counts["tool_refused"],
    }


def evaluate(root: Path, corpus: dict, output_dir: Path) -> dict:
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported source-row fallback corpus schema_version")
    for name, artifact in corpus["artifacts"].items():
        path = root / artifact["path"]
        if _sha256(path) != artifact["sha256"]:
            raise ValueError(f"source-row fallback artifact hash mismatch: {name}")

    old_dir = root / corpus["old_dir"]
    new_dir = root / corpus["new_dir"]
    old_report = json.loads((old_dir / "report.json").read_text())
    new_report = json.loads((new_dir / "report.json").read_text())
    if old_report["source_sha256"] != corpus["source_sha256"]:
        raise ValueError("old source-row report source hash mismatch")
    if new_report["source_sha256"] != corpus["source_sha256"]:
        raise ValueError("new source-row report source hash mismatch")

    old_cells = _cells(old_dir / "rows.jsonl")
    new_cells = _cells(new_dir / "rows.jsonl")
    old_candidates = {
        sample_id: cell
        for sample_id, cell in old_cells.items()
        if cell.get("candidate_value") is not None
    }
    new_candidates = {
        sample_id: cell
        for sample_id, cell in new_cells.items()
        if cell.get("candidate_value") is not None
    }
    outcomes = Counter(
        _prior_outcome(cell, new_cells.get(sample_id))
        for sample_id, cell in old_candidates.items()
    )
    additions = set(new_candidates) - set(old_candidates)
    added_readers = Counter(
        new_candidates[sample_id]["accepted_reader"] for sample_id in additions
    )

    adjudications_path = root / corpus["artifacts"]["adjudications"]["path"]
    adjudications = json.loads(adjudications_path.read_text())
    report = {
        "schema_version": 1,
        "method": "source_row_projection_fallback_differential",
        "contract": {
            "reference": "all non-null v2 candidate values",
            "agree": "the v3 candidate exists with the identical string value",
            "disagree": "the v3 candidate exists with a changed string value",
            "tool_refused": "the v3 candidate is absent or null",
            "coverage": "v3 candidates absent from v2 are reported separately",
        },
        "source_sha256": corpus["source_sha256"],
        "old_candidates": len(old_candidates),
        "new_candidates": len(new_candidates),
        "prior_candidates": {
            "checked": len(old_candidates),
            "agree": outcomes["agree"],
            "disagree": outcomes["disagree"],
            "tool_refused": outcomes["tool_refused"],
        },
        "added_candidates": len(additions),
        "added_candidate_readers": dict(sorted(added_readers.items())),
        "integrated_report": {
            key: new_report[key]
            for key in (
                "recovery_rows_key_confirmed",
                "control_rows_key_confirmed",
                "alignment_rows_key_confirmed",
                "cell_statuses",
                "accepted_readers",
                "control",
                "source_labels",
            )
        },
        "source_adjudications": _score_adjudications(
            adjudications, corpus["source_sha256"], new_cells
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    corpus = json.loads(args.corpus.read_text())
    report = evaluate(args.root, corpus, args.output)
    prior = report["prior_candidates"]
    print(
        f"source-row fallback: {prior['agree']}/{prior['checked']} prior candidates "
        f"preserved; {prior['disagree']} changed, {prior['tool_refused']} refused; "
        f"{report['added_candidates']} added"
    )
    if args.check and {
        key: report[key]
        for key in (
            "old_candidates",
            "new_candidates",
            "prior_candidates",
            "added_candidates",
            "added_candidate_readers",
            "integrated_report",
            "source_adjudications",
        )
    } != corpus.get("expected"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
