"""Score pdf2md table outputs against pdf-parse-bench numeric ground truth.

This is a deterministic diagnostic, not the benchmark's official LLM-judge score.
It reports exact numeric multisets, partial matches, and missing extracted tables.
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

from pdf2md.tables import html_tables


_NUMBER = re.compile(
    r"(?<![\w.])[-+−–—]?(?:\d{1,3}(?:[ ,]\d{3})+|\d+)(?:\.\d+)?"
    r"(?:[eE][-+]?\d+)?(?![\w.])"
)


def _numeric_tokens(text: str) -> Counter[str]:
    tokens = Counter()
    for match in _NUMBER.finditer(text):
        raw = match.group(0).replace("−", "-").replace("–", "-").replace("—", "-")
        raw = raw.replace(",", "").replace(" ", "")
        try:
            value = Decimal(raw)
        except InvalidOperation:
            continue
        tokens[str(value.normalize()) if value else "0"] += 1
    return tokens


def _latex_numeric_tokens(text: str) -> Counter[str]:
    cleaned = re.sub(r"\\(?:begin|end)\{tabular\}\{[^{}]*\}", " ", text)
    cleaned = re.sub(r"\\(?:cline|cmidrule)(?:\([^)]*\))?\{[^{}]*\}", " ", cleaned)
    for _ in range(3):
        cleaned = re.sub(
            r"\\(?:multirow|multicolumn)\{[^{}]*\}\{[^{}]*\}\{([^{}]*)\}",
            r"\1",
            cleaned,
        )
    return _numeric_tokens(cleaned)


def _latest_versions(output_dir: Path) -> list[Path]:
    versions = []
    for document_dir in output_dir.iterdir():
        if not document_dir.is_dir():
            continue
        candidates = sorted(
            document_dir.glob("v*"),
            key=lambda path: int(path.name[1:]) if path.name[1:].isdigit() else -1,
            reverse=True,
        )
        version = next(
            (path for path in candidates if (path / "provenance.json").is_file()), None
        )
        if version is not None:
            versions.append(version)
    return versions


def _extracted_tables(version_dir: Path) -> list[dict]:
    manifest = json.loads((version_dir / "manifest.json").read_text())
    tables = []
    for entry in manifest.get("representations", {}).get("tables", []):
        relative = entry.get("json")
        if relative and (version_dir / relative).is_file():
            record = json.loads((version_dir / relative).read_text())
            tables.append({"block_id": entry["block_id"], "rows": record.get("rows") or []})
    return tables


def _overlap(left: Counter[str], right: Counter[str]) -> int:
    return sum((left & right).values())


def _match_tables(ground: list[Counter[str]], extracted: list[Counter[str]]) -> list[dict]:
    @cache
    def assign(ground_index: int, used: int) -> tuple[int, tuple[int | None, ...]]:
        if ground_index == len(ground):
            return 0, ()
        best_score, tail = assign(ground_index + 1, used)
        best = (best_score, (None, *tail))
        expected = ground[ground_index]
        for extracted_index, actual in enumerate(extracted):
            if used & (1 << extracted_index):
                continue
            common = _overlap(expected, actual)
            if not common:
                continue
            tail_score, tail = assign(
                ground_index + 1, used | (1 << extracted_index)
            )
            count_delta = abs(sum(expected.values()) - sum(actual.values()))
            candidate = (
                tail_score + common * 100_000 - count_delta,
                (extracted_index, *tail),
            )
            if candidate[0] > best[0]:
                best = candidate
        return best

    _, assignments = assign(0, 0)
    records = []

    for ground_index, expected in enumerate(ground):
        match = assignments[ground_index]
        if not expected:
            records.append({"ground_table": ground_index, "outcome": "not_applicable"})
            continue
        if match is None:
            records.append({
                "ground_table": ground_index,
                "outcome": "tool_refused",
                "expected_numeric_tokens": sum(expected.values()),
            })
            continue
        actual = extracted[match]
        common = _overlap(expected, actual)
        expected_count = sum(expected.values())
        actual_count = sum(actual.values())
        records.append({
            "ground_table": ground_index,
            "extracted_table": match,
            "outcome": "agree" if expected == actual else "disagree",
            "expected_numeric_tokens": expected_count,
            "actual_numeric_tokens": actual_count,
            "matched_numeric_tokens": common,
            "recall": round(common / expected_count, 4),
            "precision": round(common / actual_count, 4) if actual_count else 0.0,
        })
    return records


def _ground_tables(path: Path) -> list[Counter[str]]:
    return [
        _latex_numeric_tokens(entry["data"])
        for entry in json.loads(path.read_text())
        if entry.get("type") == "table"
    ]


def _report(documents: list[dict], ground_truth_paths: list[Path]) -> dict:
    digest = hashlib.sha256()
    for path in sorted(ground_truth_paths):
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    records = [record for document in documents for record in document["records"]]
    numeric_records = [record for record in records if record["outcome"] != "not_applicable"]
    return {
        "schema_version": 1,
        "metric": "numeric_multiset_diagnostic_not_official_pdf_parse_bench_score",
        "ground_truth_sha256": digest.hexdigest(),
        "documents": documents,
        "summary": {
            "documents": len(documents),
            "ground_tables": sum(document["ground_tables"] for document in documents),
            "extracted_tables": sum(document["extracted_tables"] for document in documents),
            "numeric_tables": len(numeric_records),
            "agree": sum(record["outcome"] == "agree" for record in numeric_records),
            "disagree": sum(record["outcome"] == "disagree" for record in numeric_records),
            "tool_refused": sum(
                record["outcome"] == "tool_refused" for record in numeric_records
            ),
            "numeric_recall": round(
                sum(record.get("matched_numeric_tokens", 0) for record in numeric_records)
                / sum(record.get("expected_numeric_tokens", 0) for record in numeric_records),
                4,
            ) if numeric_records else None,
        },
    }


def evaluate(output_dir: Path, ground_truth_dir: Path) -> dict:
    documents = []
    selected_ground_truth = []
    for version_dir in _latest_versions(output_dir):
        provenance = json.loads((version_dir / "provenance.json").read_text())
        document_id = Path(provenance["source_path"]).stem
        ground_path = ground_truth_dir / f"{document_id}.json"
        if not ground_path.is_file():
            continue
        selected_ground_truth.append(ground_path)
        ground = _ground_tables(ground_path)
        extracted_records = _extracted_tables(version_dir)
        extracted = [
            _numeric_tokens("\n".join("\t".join(row) for row in table["rows"]))
            for table in extracted_records
        ]
        records = _match_tables(ground, extracted)
        for record in records:
            table_index = record.get("extracted_table")
            if table_index is not None:
                record["block_id"] = extracted_records[table_index]["block_id"]
        documents.append({
            "document_id": document_id,
            "version_dir": str(version_dir.resolve()),
            "ground_tables": len(ground),
            "extracted_tables": len(extracted),
            "records": records,
        })

    return _report(documents, selected_ground_truth)


def evaluate_ocrflux(markdown_dir: Path, ground_truth_dir: Path) -> dict:
    documents = []
    selected_ground_truth = []
    for markdown_path in sorted(markdown_dir.rglob("*.md")):
        document_id = markdown_path.stem
        ground_path = ground_truth_dir / f"{document_id}.json"
        if not ground_path.is_file():
            continue
        selected_ground_truth.append(ground_path)
        ground = _ground_tables(ground_path)
        extracted_rows = html_tables(markdown_path.read_text())
        extracted = [
            _numeric_tokens("\n".join("\t".join(row) for row in table))
            for table in extracted_rows
        ]
        records = _match_tables(ground, extracted)
        for record in records:
            table_index = record.get("extracted_table")
            if table_index is not None:
                record["block_id"] = f"ocrflux/table/{table_index}"
        documents.append({
            "document_id": document_id,
            "markdown": str(markdown_path.resolve()),
            "ground_tables": len(ground),
            "extracted_tables": len(extracted),
            "records": records,
        })
    return _report(documents, selected_ground_truth)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a deterministic numeric diagnostic on pdf-parse-bench tables."
    )
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("ground_truth_dir", type=Path)
    parser.add_argument("--format", choices=("pdf2md", "ocrflux"), default="pdf2md")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    report = (
        evaluate_ocrflux(args.output_dir, args.ground_truth_dir)
        if args.format == "ocrflux"
        else evaluate(args.output_dir, args.ground_truth_dir)
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    summary = report["summary"]
    print(
        f"pdf-parse-bench numeric diagnostic: {summary['agree']}/{summary['numeric_tables']} "
        f"tables exact, {summary['disagree']} partial, {summary['tool_refused']} refused; "
        f"token recall {summary['numeric_recall']}"
    )


if __name__ == "__main__":
    main()
