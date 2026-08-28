"""Mine table cells where a primary extraction and Tesseract disagree.

The report is a source-linked review queue, not ground truth. Source inspection is
required before any record is copied into a labelled accuracy corpus.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from eval_numeric_tables import (
    _candidate_tables,
    _table_crops,
    _table_pages,
    _tesseract_reference,
)
from pdf2md.table_verify import numeric_values_equal, typed_value


def evaluate(version_dir: Path, executable: str) -> dict:
    provenance = json.loads((version_dir / "provenance.json").read_text())
    tables = _candidate_tables(version_dir)
    pages = _table_pages(version_dir)
    crops = _table_crops(version_dir)
    cells = [
        {
            "block_id": block_id,
            "row": row_index,
            "column": column_index,
            "primary_value": value,
        }
        for block_id, rows in tables.items()
        for row_index, row in enumerate(rows)
        for column_index, value in enumerate(row)
        if typed_value(value)[2] == "numeric"
    ]
    readings, refused_blocks = _tesseract_reference(
        version_dir, tables, cells, executable
    )
    refused = set(refused_blocks)
    records = []
    for cell in cells:
        block_id = cell["block_id"]
        key = (block_id, cell["row"], cell["column"])
        reader_value = readings.get(key)
        if reader_value is None:
            outcome = "tool_refused"
            if block_id not in crops:
                refusal_reason = "source_crop_missing"
            elif block_id in refused:
                refusal_reason = "grid_alignment_failed"
            else:
                refusal_reason = "cell_alignment_missing"
        else:
            outcome = (
                "agree"
                if numeric_values_equal(cell["primary_value"], reader_value)
                else "disagree"
            )
            refusal_reason = None
        crop = crops.get(block_id)
        records.append({
            "page": pages.get(block_id),
            **cell,
            "reader_value": reader_value,
            "outcome": outcome,
            "refusal_reason": refusal_reason,
            "source_crop": (
                crop.relative_to(version_dir).as_posix()
                if crop is not None and crop.is_relative_to(version_dir)
                else None
            ),
        })
    counts = {
        outcome: sum(record["outcome"] == outcome for record in records)
        for outcome in ("agree", "disagree", "tool_refused")
    }
    version = subprocess.run(
        [executable, "--version"], capture_output=True, text=True, check=False
    ).stdout.splitlines()[0]
    return {
        "schema_version": 1,
        "method": "primary_tesseract_numeric_disagreement_miner",
        "contract": {
            "purpose": "candidate discovery only; source inspection supplies labels",
            "comparison": "exact numeric value after OCR normalization",
            "outcomes": ["agree", "disagree", "tool_refused"],
        },
        "source": provenance["source_path"],
        "source_sha256": provenance["source_sha256"],
        "version": version_dir.name,
        "reference": {"name": "tesseract", "version": version},
        "checked": len(records),
        **counts,
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mine source-linked primary/Tesseract numeric disagreements."
    )
    parser.add_argument("version_dir", type=Path)
    parser.add_argument("--tesseract", default="tesseract")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    executable = shutil.which(args.tesseract)
    if executable is None:
        print(f"Tesseract unavailable: {args.tesseract}")
        raise SystemExit(2)
    report = evaluate(args.version_dir, executable)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"numeric cells: {report['agree']}/{report['checked']} agree, "
        f"{report['disagree']} disagree, {report['tool_refused']} tool-refused"
    )


if __name__ == "__main__":
    main()
