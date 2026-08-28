"""Compare source-pixel panel bounds with pinned key-cell panel identities."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image

from pdf2md.line_reader import _sha256
from pdf2md.row_locator import projection_panel_bounds


ROOT = Path(__file__).parent.parent
DEFAULT_CORPUS = ROOT / "tests" / "projection_panel_corpus.json"
DEFAULT_OUTPUT = ROOT / "out" / "reviews" / "projection-panel-corpus-v1"


def _mismatched_records(
    bounds: list[tuple[int, int]], records: list[dict]
) -> list[str]:
    mismatches = []
    for record in records:
        panel = int(record["panel"])
        if panel >= len(bounds):
            mismatches.append(record["id"])
            continue
        center = (float(record["source_box"][0]) + record["source_box"][2]) / 2
        left, right = bounds[panel]
        if not left <= center < right:
            mismatches.append(record["id"])
    return mismatches


def evaluate(root: Path, corpus: dict, output_dir: Path) -> dict:
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported projection-panel corpus schema_version")
    manifest_path = root / corpus["manifest"]
    if _sha256(manifest_path) != corpus["manifest_sha256"]:
        raise ValueError("projection-panel manifest hash mismatch")
    manifest = json.loads(manifest_path.read_text())
    if manifest["source_sha256"] != corpus["source_sha256"]:
        raise ValueError("projection-panel source hash mismatch")

    records_by_block = defaultdict(list)
    for record in manifest["records"]:
        records_by_block[record["source_block_id"]].append(record)

    table_reports = []
    for index, table in enumerate(manifest["tables"], start=1):
        block_id = table["source_block_id"]
        print(f"[{index}/{len(manifest['tables'])}] {block_id}", flush=True)
        records = records_by_block[block_id]
        if not records:
            table_reports.append({
                "source_block_id": block_id,
                "page": table["page"],
                "layout_family": table["layout_family"],
                "panel_count": table["panel_count"],
                "key_cells_checked": 0,
                "outcome": "no_reference",
                "mismatched_key_cells": [],
                "refusal": None,
                "detection": None,
                "source_crop": None,
                "source_crop_sha256": None,
            })
            continue
        crop_paths = {record["source_crop"] for record in records}
        crop_hashes = {record["source_crop_sha256"] for record in records}
        if len(crop_paths) != 1 or len(crop_hashes) != 1:
            raise ValueError(f"inconsistent source crop identity for {block_id}")
        crop_path = manifest_path.parent / crop_paths.pop()
        crop_sha256 = crop_hashes.pop()
        if _sha256(crop_path) != crop_sha256:
            raise ValueError(f"source crop hash mismatch for {block_id}")

        with Image.open(crop_path) as image:
            bounds, evidence, refusal = projection_panel_bounds(
                image, int(table["panel_count"])
            )
        mismatches = (
            _mismatched_records(bounds, records) if bounds is not None else []
        )
        outcome = (
            "tool_refused"
            if bounds is None
            else "disagree" if mismatches else "agree"
        )
        table_reports.append({
            "source_block_id": block_id,
            "page": table["page"],
            "layout_family": table["layout_family"],
            "panel_count": table["panel_count"],
            "key_cells_checked": len(records),
            "outcome": outcome,
            "mismatched_key_cells": mismatches,
            "refusal": refusal,
            "detection": evidence,
            "source_crop": crop_path.relative_to(root).as_posix(),
            "source_crop_sha256": crop_sha256,
        })

    counts = Counter(table["outcome"] for table in table_reports)
    families = {}
    for family in sorted({table["layout_family"] for table in table_reports}):
        family_tables = [
            table for table in table_reports if table["layout_family"] == family
        ]
        family_counts = Counter(table["outcome"] for table in family_tables)
        families[family] = {
            "tables": len(family_tables),
            "agree": family_counts["agree"],
            "disagree": family_counts["disagree"],
            "tool_refused": family_counts["tool_refused"],
            "no_reference": family_counts["no_reference"],
        }
    report = {
        "schema_version": 1,
        "method": "source_pixel_projection_panel_boundary_comparison",
        "contract": {
            "reference": (
                "hash-pinned source boxes and structural panel identities from the "
                "independent key-cell locator"
            ),
            "tool": "vertical-whitespace projection receives source pixels only",
            "outcomes": ["agree", "disagree", "tool_refused"],
            "skip": "no_reference",
        },
        "corpus_sha256": corpus["_corpus_sha256"],
        "manifest_sha256": corpus["manifest_sha256"],
        "source_sha256": corpus["source_sha256"],
        "tables_in_corpus": len(table_reports),
        "tables_checked": len(table_reports) - counts["no_reference"],
        "key_cells_checked": sum(
            table["key_cells_checked"] for table in table_reports
        ),
        "agree": counts["agree"],
        "disagree": counts["disagree"],
        "tool_refused": counts["tool_refused"],
        "no_reference": counts["no_reference"],
        "mismatched_key_cells": sum(
            len(table["mismatched_key_cells"]) for table in table_reports
        ),
        "families": families,
        "tables": table_reports,
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
        f"projection panels: {report['agree']}/{report['tables_checked']} agree; "
        f"{report['disagree']} disagree, {report['tool_refused']} refused; "
        f"{report['key_cells_checked']} key cells checked"
    )
    if args.check:
        actual = {
            key: report[key]
            for key in (
                "tables_checked",
                "key_cells_checked",
                "agree",
                "disagree",
                "tool_refused",
                "no_reference",
                "mismatched_key_cells",
                "families",
            )
        }
        if actual != corpus.get("expected"):
            print(json.dumps(
                {"expected": corpus.get("expected"), "actual": actual}, indent=2
            ))
            raise SystemExit(1)


if __name__ == "__main__":
    main()
