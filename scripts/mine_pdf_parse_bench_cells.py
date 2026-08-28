"""Mine source-grounded numeric-cell errors from pdf-parse-bench LaTeX tables.

Only rows with equal scalar-cell counts and all but at most one value already
matching are compared. Structural omissions remain refusals, not guessed mappings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from functools import cache
from pathlib import Path

from eval_pdf_parse_bench import (
    _extracted_tables,
    _latest_versions,
    _match_tables,
)
from pdf2md.table_verify import _numeric_read, typed_value


_ROW_BREAK = re.compile(r"\\\\(?:\s*\[[^\]]*\])?")
_NUMBER = re.compile(r"[-+]?(?:\d+(?:[ ,]\d{3})*(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?")
_UNWRAP = re.compile(
    r"\\(?:textbf|mathbf|mathrm|mathit|mathsf|mathtt|emph|underline)\{([^{}]*)\}"
)


def _normalized_number(text: str) -> str | None:
    raw = text.replace("−", "-").replace("–", "-").replace("—", "-")
    raw = raw.replace(",", "").replace(" ", "")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return str(value.normalize()) if value else "0"


def _latex_scalar(cell: str) -> str | None:
    cleaned = re.sub(r"\\(?:hphantom|vphantom|phantom)\{[^{}]*\}", "", cell)
    cleaned = re.sub(r"\\color\{[^{}]*\}", "", cleaned)
    cleaned = re.sub(r"\\textsuperscript\{[^{}]*\}", "", cleaned)
    for _ in range(4):
        cleaned = _UNWRAP.sub(r"\1", cleaned)
    cleaned = cleaned.replace("$", "").replace("{", "").replace("}", "")
    cleaned = cleaned.replace("~", "").strip()
    match = _NUMBER.fullmatch(cleaned)
    return _normalized_number(match.group(0)) if match else None


def _latex_scalar_rows(text: str) -> list[list[tuple[int, str]]]:
    rows = []
    for raw_row in _ROW_BREAK.split(text):
        raw_row = re.sub(
            r"\\(?:toprule|midrule|bottomrule|hline|cline|cmidrule)(?:\([^)]*\))?"
            r"(?:\{[^{}]*\})?",
            "",
            raw_row,
        )
        cells = []
        for column, cell in enumerate(raw_row.split("&")):
            value = _latex_scalar(cell)
            if value is not None:
                cells.append((column, value))
        if cells:
            rows.append(cells)
    return rows


def _extracted_scalar_rows(rows: list[list[str]]) -> list[tuple[int, list[tuple[int, str]]]]:
    scalar_rows = []
    for row_index, row in enumerate(rows):
        cells = []
        for column, value in enumerate(row):
            if typed_value(value)[2] == "numeric":
                cells.append((column, _normalized_number(_numeric_read(value))))
        cells = [(column, value) for column, value in cells if value is not None]
        if cells:
            scalar_rows.append((row_index, cells))
    return scalar_rows


def _scalar_counter(rows: list[list[tuple[int, str]]]) -> Counter[str]:
    return Counter(value for row in rows for _, value in row)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ground_truth_by_source_sha(ground_truth_dir: Path) -> dict[str, Path]:
    pdf_dir = ground_truth_dir.parent / "pdfs"
    if not pdf_dir.is_dir():
        return {}
    return {
        _sha256(pdf_path): ground_path
        for pdf_path in pdf_dir.glob("*.pdf")
        if (ground_path := ground_truth_dir / f"{pdf_path.stem}.json").is_file()
    }


def _row_matches(
    ground_rows: list[list[tuple[int, str]]],
    extracted_rows: list[tuple[int, list[tuple[int, str]]]],
) -> list[tuple[int, int, int]]:
    ground = [Counter(value for _, value in row) for row in ground_rows]
    extracted = [Counter(value for _, value in row) for _, row in extracted_rows]

    @cache
    def align(
        ground_row: int, extracted_row: int
    ) -> tuple[int, tuple[tuple[int, int, int], ...]]:
        if ground_row == len(ground) or extracted_row == len(extracted):
            return 0, ()
        candidates = [align(ground_row + 1, extracted_row)]
        candidates.append(align(ground_row, extracted_row + 1))
        expected = ground_rows[ground_row]
        actual = extracted_rows[extracted_row][1]
        overlap = sum((ground[ground_row] & extracted[extracted_row]).values())
        if len(expected) >= 2 and len(expected) == len(actual) and overlap >= len(expected) - 1:
            score, tail = align(ground_row + 1, extracted_row + 1)
            candidates.append((
                score + overlap * 1_000 + 1,
                ((ground_row, extracted_row, overlap), *tail),
            ))
        return max(candidates, key=lambda candidate: (candidate[0], len(candidate[1])))

    return list(align(0, 0)[1])


def evaluate(output_dir: Path, ground_truth_dir: Path) -> dict:
    documents = []
    cells = []
    ground_truth_by_sha = _ground_truth_by_source_sha(ground_truth_dir)
    for version_dir in _latest_versions(output_dir):
        provenance = json.loads((version_dir / "provenance.json").read_text())
        manifest = json.loads((version_dir / "manifest.json").read_text())
        table_pages = {
            entry["block_id"]: entry["page"]
            for entry in manifest.get("representations", {}).get("tables", [])
        }
        document_id = Path(provenance["source_path"]).stem
        ground_path = ground_truth_dir / f"{document_id}.json"
        if not ground_path.is_file():
            ground_path = ground_truth_by_sha.get(provenance["source_sha256"])
            if ground_path is None:
                continue
            document_id = ground_path.stem
        ground_entries = [
            entry["data"]
            for entry in json.loads(ground_path.read_text())
            if entry.get("type") == "table"
        ]
        extracted_tables = _extracted_tables(version_dir)
        ground_scalar_rows = [_latex_scalar_rows(entry) for entry in ground_entries]
        extracted_scalar_rows = [
            _extracted_scalar_rows(table["rows"]) for table in extracted_tables
        ]
        table_matches = _match_tables(
            [_scalar_counter(rows) for rows in ground_scalar_rows],
            [
                _scalar_counter([cells for _, cells in rows])
                for rows in extracted_scalar_rows
            ],
        )
        document_cells = []
        row_refusals = 0
        for ground_table, table_match in enumerate(table_matches):
            extracted_table = table_match.get("extracted_table")
            if extracted_table is None:
                continue
            ground_rows = ground_scalar_rows[ground_table]
            extracted_rows = extracted_scalar_rows[extracted_table]
            matches = _row_matches(ground_rows, extracted_rows)
            row_refusals += len(ground_rows) - len(matches)
            for ground_row, extracted_row, overlap in matches:
                source_row, actual_cells = extracted_rows[extracted_row]
                expected_cells = ground_rows[ground_row]
                for position, ((_, expected), (source_column, actual)) in enumerate(
                    zip(expected_cells, actual_cells)
                ):
                    record = {
                        "source_sha256": provenance["source_sha256"],
                        "version": version_dir.name,
                        "document_id": document_id,
                        "page": table_pages[extracted_tables[extracted_table]["block_id"]],
                        "ground_truth": str(ground_path),
                        "ground_table": ground_table,
                        "ground_row": ground_row,
                        "block_id": extracted_tables[extracted_table]["block_id"],
                        "source_row": source_row,
                        "source_column": source_column,
                        "position": position,
                        "expected": expected,
                        "actual": actual,
                        "row_scalar_cells": len(expected_cells),
                        "row_matched_cells": overlap,
                        "outcome": "agree" if expected == actual else "disagree",
                    }
                    document_cells.append(record)
                    cells.append(record)
        documents.append({
            "document_id": document_id,
            "source": provenance["source_path"],
            "source_sha256": provenance["source_sha256"],
            "version": version_dir.name,
            "ground_truth": str(ground_path),
            "ground_truth_sha256": _sha256(ground_path),
            "checked_cells": len(document_cells),
            "disagree": sum(cell["outcome"] == "disagree" for cell in document_cells),
            "row_refusals": row_refusals,
        })
    return {
        "schema_version": 1,
        "method": "pdf_parse_bench_scalar_cell_miner",
        "contract": {
            "table_mapping": "maximum_numeric_multiset_overlap",
            "row_mapping": "equal_scalar_count_and_all_but_at_most_one_value_match",
            "cell_mapping": "numeric_scalar_position_within_mapped_row",
        },
        "documents": documents,
        "checked": len(cells),
        "agree": sum(cell["outcome"] == "agree" for cell in cells),
        "disagree": sum(cell["outcome"] == "disagree" for cell in cells),
        "cells": cells,
    }


def labels_from_report(report: dict) -> dict:
    cells_by_document: dict[str, list[dict]] = {}
    for cell in report["cells"]:
        cells_by_document.setdefault(cell["document_id"], []).append({
            "page": cell["page"],
            "block_id": cell["block_id"],
            "row": cell["source_row"],
            "column": cell["source_column"],
            "expected": cell["expected"],
            "label": (
                f"pdf-parse-bench {cell['document_id']} table {cell['ground_table']} "
                f"row {cell['ground_row']} scalar {cell['position']}"
            ),
        })
    return {
        "schema_version": 1,
        "method": "pdf_parse_bench_scalar_cells",
        "documents": [
            {
                "source_sha256": document["source_sha256"],
                "source": document["source"],
                "version": document["version"],
                "ground_truth": document["ground_truth"],
                "ground_truth_sha256": document["ground_truth_sha256"],
                "cells": cells_by_document.get(document["document_id"], []),
            }
            for document in report["documents"]
            if cells_by_document.get(document["document_id"])
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mine high-confidence numeric-cell errors from pdf-parse-bench."
    )
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("ground_truth_dir", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--labels", type=Path)
    args = parser.parse_args()
    report = evaluate(args.output_dir, args.ground_truth_dir)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    if args.labels:
        args.labels.parent.mkdir(parents=True, exist_ok=True)
        args.labels.write_text(json.dumps(labels_from_report(report), indent=2) + "\n")
    print(
        f"pdf-parse-bench cells: {report['agree']}/{report['checked']} agree, "
        f"{report['disagree']} disagree"
    )


if __name__ == "__main__":
    main()
