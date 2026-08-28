"""Load and compare semantic external references for extracted table cells.

Reference matching stays separate from OCR-reader evidence: a missing semantic key
is `no_reference`, while a missing extracted cell is `tool_refused`.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from pdf2md.schema import TableData


def compare_external_reference(
    version_dir: Path, reference_path: Path | None
) -> dict[str, object]:
    """Compare a semantic reference CSV with normalized output for a release gate."""
    if reference_path is None:
        return {
            "schema_version": 1,
            "reference": None,
            "checked": 0,
            "agree": 0,
            "disagree": 0,
            "tool_refused": 0,
            "no_reference": 1,
            "records": [],
        }
    references = load_external_reference(reference_path)
    extracted = {}
    for path in sorted((version_dir / "data" / "tables").glob("page_*_panels.csv")):
        with path.open(newline="") as stream:
            for row in csv.DictReader(stream):
                if row.get("value_status") != "numeric" or not row.get("atomic_number"):
                    continue
                key = (row["atomic_number"], row["row_key"], row["column"])
                extracted[key] = row["raw_value"]

    records = []
    for key, expected in references.items():
        actual = extracted.get(key)
        if actual is None:
            outcome = "tool_refused"
        elif _reference_read(actual) == _reference_read(expected):
            outcome = "agree"
        else:
            outcome = "disagree"
        records.append({
            "atomic_number": key[0],
            "row_key": key[1],
            "column": key[2],
            "expected": expected,
            "actual": actual,
            "outcome": outcome,
        })
    counts = Counter(record["outcome"] for record in records)
    return {
        "schema_version": 1,
        "reference": str(reference_path.resolve()),
        "checked": len(records),
        "agree": counts["agree"],
        "disagree": counts["disagree"],
        "tool_refused": counts["tool_refused"],
        "no_reference": 0,
        "records": records,
    }


def load_external_reference(path: Path) -> dict[tuple[str, str, str], str]:
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    required = {"atomic_number", "row_key", "column", "value"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(
            "table reference CSV must contain atomic_number,row_key,column,value"
        )
    references = {}
    for row in rows:
        key = (
            row["atomic_number"].strip(),
            row["row_key"].strip(),
            row["column"].strip(),
        )
        if key in references:
            raise ValueError(f"duplicate table reference key: {key}")
        references[key] = row["value"].strip()
    return references


def normalized_semantics(
    tables: list[TableData], version_dir: Path
) -> dict[tuple[str, int, int], dict[str, str]]:
    paths = {table.normalized_data_path for table in tables if table.normalized_data_path}
    semantics = {}
    for relative in paths:
        with (version_dir / relative).open(newline="") as stream:
            for row in csv.DictReader(stream):
                key = (
                    row["source_block_id"], int(row["source_row"]), int(row["source_column"])
                )
                semantics[key] = {
                    "atomic_number": row["atomic_number"],
                    "symbol": row["symbol"],
                    "term": row["term"],
                    "configuration": row["configuration"],
                    "row_key": row["row_key"],
                    "column": row["column"],
                }
    return semantics


def external_outcome(
    semantic: dict[str, str],
    raw_value: str,
    references: dict[tuple[str, str, str], str],
    requested: bool,
) -> tuple[str | None, str]:
    if not semantic:
        return None, "not_applicable"
    if not requested:
        return None, "no_reference"
    key = (semantic["atomic_number"], semantic["row_key"], semantic["column"])
    reference = references.get(key)
    if reference is None:
        return None, "no_reference"
    outcome = "agree" if _reference_read(raw_value) == _reference_read(reference) else "disagree"
    return reference, outcome


def _reference_read(text: str) -> str:
    return text.strip().replace("−", "-").replace("–", "-").replace("—", "-")
