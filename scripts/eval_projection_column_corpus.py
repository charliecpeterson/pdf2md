"""Compare source-pixel column runs with pinned cell-box assignments."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image

from pdf2md.line_reader import _sha256
from pdf2md.row_locator import (
    projection_column_runs,
    projection_panel_bounds,
    projection_row_bands,
)


ROOT = Path(__file__).parent.parent
DEFAULT_CORPUS = ROOT / "tests" / "projection_column_corpus.json"
DEFAULT_OUTPUT = ROOT / "out" / "reviews" / "projection-column-corpus-v1"


def _reference_sha256(records: list[dict]) -> str:
    reference = [
        (
            record["id"],
            record["crop_sha256"],
            record["source_crop_sha256"],
            record["source_box"],
        )
        for record in records
    ]
    payload = json.dumps(reference, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def _cell_outcome(
    record: dict,
    panel: dict,
    column_rows: list[list[tuple[int, int]] | None] | None,
) -> tuple[str, list[int] | None]:
    position = int(record["source_position"])
    if column_rows is None or position >= len(column_rows):
        return "tool_refused", None
    row = column_rows[position]
    offset = int(record["source_column"]) - int(panel["source_column"])
    if row is None or not 0 <= offset < len(row):
        return "tool_refused", None
    run = row[offset]
    center = sum(run) / 2
    left, _, right, _ = record["source_box"]
    return ("agree" if left <= center < right else "disagree"), list(run)


def evaluate(root: Path, corpus: dict, output_dir: Path) -> dict:
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported projection-column corpus schema_version")
    recovery_dir = root / corpus["recovery_dir"]
    manifest = json.loads((recovery_dir / "manifest.json").read_text())
    if manifest["source_sha256"] != corpus["source_sha256"]:
        raise ValueError("projection-column source hash mismatch")
    if _reference_sha256(manifest["records"]) != corpus["reference_sha256"]:
        raise ValueError("projection-column reference geometry changed")

    records_by_panel = defaultdict(list)
    for record in manifest["records"]:
        records_by_panel[record["source_block_id"], int(record["panel"])].append(
            record
        )
    panels_by_block = defaultdict(list)
    for panel in manifest["panels"]:
        panels_by_block[panel["source_block_id"]].append(panel)

    panel_reports = []
    findings = []
    counts = Counter()
    for block_index, (block_id, panels) in enumerate(
        sorted(panels_by_block.items()), start=1
    ):
        print(f"[{block_index}/{len(panels_by_block)}] {block_id}", flush=True)
        panels.sort(key=lambda panel: int(panel["panel"]))
        stem = block_id.strip("#/").replace("/", "_")
        crop_path = recovery_dir / "source-crops" / f"{stem}.png"
        referenced_records = [
            record
            for panel in panels
            for record in records_by_panel[block_id, int(panel["panel"])]
        ]
        if referenced_records:
            crop_hashes = {
                record["source_crop_sha256"] for record in referenced_records
            }
            if len(crop_hashes) != 1 or _sha256(crop_path) != crop_hashes.pop():
                raise ValueError(f"source crop hash mismatch for {block_id}")

        with Image.open(crop_path) as image:
            panel_bounds, panel_evidence, panel_refusal = projection_panel_bounds(
                image, len(panels)
            )
            for panel in panels:
                panel_index = int(panel["panel"])
                records = records_by_panel[block_id, panel_index]
                if not records:
                    counts["no_reference"] += 1
                    panel_reports.append({
                        "source_block_id": block_id,
                        "page": panel["page"],
                        "panel": panel_index,
                        "columns": len(panel["columns"]),
                        "reference_cells": 0,
                        "outcome": "no_reference",
                        "panel_detection": panel_evidence,
                        "panel_refusal": panel_refusal,
                    })
                    continue
                if panel_bounds is None:
                    row_bands = None
                    row_evidence = None
                    row_refusal = panel_refusal
                    column_rows = None
                    column_evidence = None
                    column_refusal = panel_refusal
                else:
                    row_bands, row_evidence, row_refusal = projection_row_bands(
                        image,
                        int(panel["canonical_rows"]),
                        panel_index=panel_index,
                        panel_count=len(panels),
                        stripe_fraction=1 / len(panel["columns"]),
                        panel_bounds=panel_bounds,
                    )
                    if row_bands is None:
                        column_rows = None
                        column_evidence = None
                        column_refusal = row_refusal
                    else:
                        column_rows, column_evidence, column_refusal = (
                            projection_column_runs(
                                image,
                                row_bands,
                                panel_bounds[panel_index],
                                len(panel["columns"]),
                            )
                        )

                panel_counts = Counter()
                for record in records:
                    outcome, run = _cell_outcome(record, panel, column_rows)
                    counts[outcome] += 1
                    panel_counts[outcome] += 1
                    if outcome != "agree":
                        findings.append({
                            "id": record["id"],
                            "outcome": outcome,
                            "source_box": record["source_box"],
                            "projection_x_run": run,
                        })
                panel_outcome = (
                    "disagree"
                    if panel_counts["disagree"]
                    else "tool_refused" if panel_counts["tool_refused"] else "agree"
                )
                panel_reports.append({
                    "source_block_id": block_id,
                    "page": panel["page"],
                    "panel": panel_index,
                    "columns": len(panel["columns"]),
                    "reference_cells": len(records),
                    "outcome": panel_outcome,
                    "agree": panel_counts["agree"],
                    "disagree": panel_counts["disagree"],
                    "tool_refused": panel_counts["tool_refused"],
                    "panel_detection": panel_evidence,
                    "panel_refusal": panel_refusal,
                    "row_projection": row_evidence,
                    "row_refusal": row_refusal,
                    "column_projection": column_evidence,
                    "column_refusal": column_refusal,
                })

    referenced_panels = [
        panel for panel in panel_reports if panel["outcome"] != "no_reference"
    ]
    report = {
        "schema_version": 1,
        "method": "source_pixel_projection_column_comparison",
        "contract": {
            "reference": (
                "hash-pinned OCR-derived cell boxes created before the independent "
                "column locator"
            ),
            "tool": (
                "source-pixel ink runs inside independently detected panel and row "
                "bounds; structural column count only"
            ),
            "outcomes": ["agree", "disagree", "tool_refused"],
            "skip": "no_reference",
        },
        "corpus_sha256": corpus["_corpus_sha256"],
        "source_sha256": corpus["source_sha256"],
        "reference_sha256": corpus["reference_sha256"],
        "panels_in_corpus": len(panel_reports),
        "panels_checked": len(referenced_panels),
        "panels_agree": sum(panel["outcome"] == "agree" for panel in referenced_panels),
        "panels_disagree": sum(
            panel["outcome"] == "disagree" for panel in referenced_panels
        ),
        "panels_tool_refused": sum(
            panel["outcome"] == "tool_refused" for panel in referenced_panels
        ),
        "panels_no_reference": counts["no_reference"],
        "cells_checked": len(manifest["records"]),
        "agree": counts["agree"],
        "disagree": counts["disagree"],
        "tool_refused": counts["tool_refused"],
        "findings": findings,
        "panels": panel_reports,
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
    corpus["_corpus_sha256"] = _sha256(args.corpus)
    report = evaluate(args.root, corpus, args.output)
    print(
        f"projection columns: {report['panels_agree']}/{report['panels_checked']} "
        f"panels agree; {report['agree']}/{report['cells_checked']} cells agree; "
        f"{report['disagree']} disagree, {report['tool_refused']} refused"
    )
    if args.check:
        actual = {
            key: report[key]
            for key in (
                "panels_in_corpus",
                "panels_checked",
                "panels_agree",
                "panels_disagree",
                "panels_tool_refused",
                "panels_no_reference",
                "cells_checked",
                "agree",
                "disagree",
                "tool_refused",
            )
        }
        if actual != corpus.get("expected"):
            print(json.dumps(
                {"expected": corpus.get("expected"), "actual": actual}, indent=2
            ))
            raise SystemExit(1)


if __name__ == "__main__":
    main()
