"""Evaluate source-declared exact relations without proposing replacement values.

The corpus pins every source and extracted artifact used by a relation. Outcomes can
support a value or request review; they never choose or synthesize a corrected value.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).parent.parent
DEFAULT_CORPUS = ROOT / "tests" / "internal_scientific_checks_corpus.json"
DEFAULT_REPORT = ROOT / "out" / "reviews" / "internal-scientific-checks-v1.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


def _csv_cell(path: Path, row: int, column: int) -> str:
    with path.open(newline="") as stream:
        rows = list(csv.reader(stream))
    try:
        return rows[row][column]
    except IndexError as exc:
        raise ValueError(f"CSV cell outside artifact: {path}:{row},{column}") from exc


def _html_cell(path: Path, table: int, row: int, column: int) -> str:
    parser = _TableParser()
    parser.feed(path.read_text())
    try:
        return parser.tables[table][row][column]
    except IndexError as exc:
        raise ValueError(
            f"HTML table cell outside artifact: {path}:{table},{row},{column}"
        ) from exc


def _regex_value(path: Path, pattern: str, group: int, occurrence: int = 0) -> str:
    matches = list(re.finditer(pattern, path.read_text(), re.MULTILINE))
    try:
        return matches[occurrence].group(group)
    except (IndexError, KeyError) as exc:
        raise ValueError(f"regex value unavailable in {path}: {pattern}") from exc


def _read_value(locator: dict, root: Path) -> str:
    path = root / locator["path"]
    kind = locator["kind"]
    if kind == "csv":
        return _csv_cell(path, locator["row"], locator["column"])
    if kind == "html_table":
        return _html_cell(
            path, locator["table"], locator["row"], locator["column"]
        )
    if kind == "regex":
        return _regex_value(
            path,
            locator["pattern"],
            locator["group"],
            locator.get("occurrence", 0),
        )
    raise ValueError(f"unsupported value locator: {kind}")


def _number(value: str) -> Decimal | None:
    try:
        return Decimal(value.strip())
    except InvalidOperation:
        return None


def _evaluate_relation(kind: str, operands: list[str]) -> tuple[str, str]:
    numbers = [_number(value) for value in operands]
    if any(value is None for value in numbers):
        return "tool_refused", "nonnumeric_operand"
    numeric = [value for value in numbers if value is not None]
    if kind == "exact_equal":
        agrees = len(numeric) >= 2 and len(set(numeric)) == 1
    elif kind == "exact_sum":
        agrees = len(numeric) >= 3 and sum(numeric[:-1]) == numeric[-1]
    elif kind == "exact_product":
        product = Decimal(1)
        for value in numeric[:-1]:
            product *= value
        agrees = len(numeric) >= 3 and product == numeric[-1]
    else:
        raise ValueError(f"unsupported exact relation: {kind}")
    return ("agree", "exact_identity") if agrees else ("disagree", "exact_identity_failed")


def _verify_artifacts(corpus: dict, root: Path) -> None:
    for document in corpus["documents"]:
        source_path = root / document["source"]
        if _sha256(source_path) != document["source_sha256"]:
            raise ValueError(f"source hash differs for {document['id']}")
        for path, expected in document["artifacts"].items():
            if _sha256(root / path) != expected:
                raise ValueError(f"artifact hash differs: {path}")


def evaluate(corpus_path: Path, root: Path = ROOT) -> dict:
    corpus = json.loads(corpus_path.read_text())
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported internal scientific checks schema_version")
    _verify_artifacts(corpus, root)
    values = {
        document["id"]: {
            name: _read_value(locator, root)
            for name, locator in document["values"].items()
        }
        for document in corpus["documents"]
    }
    records = []
    for check in corpus["checks"]:
        observed = [values[check["document"]][name] for name in check["operands"]]
        outcome, reason = _evaluate_relation(check["kind"], observed)
        records.append({
            "id": check["id"],
            "document": check["document"],
            "category": check["category"],
            "kind": check["kind"],
            "operands": check["operands"],
            "observed": observed,
            "outcome": outcome,
            "reason": reason,
            "action": "support" if outcome == "agree" else "review",
        })
    counts = Counter(record["outcome"] for record in records)
    categories = sorted({record["category"] for record in records})
    return {
        "schema_version": 1,
        "method": "source_declared_exact_relations",
        "contract": {
            "arithmetic": "decimal exactness with no tolerance",
            "nonnumeric": "tool_refused",
            "authority": "support_or_review_only",
            "replacement_values_emitted": False,
        },
        "corpus_sha256": _sha256(corpus_path),
        "documents": len(corpus["documents"]),
        "checks": len(records),
        "categories": categories,
        "outcomes": {
            "agree": counts["agree"],
            "disagree": counts["disagree"],
            "tool_refused": counts["tool_refused"],
        },
        "by_category": {
            category: dict(Counter(
                record["outcome"]
                for record in records
                if record["category"] == category
            ))
            for category in categories
        },
        "records": records,
    }


def _checked_result(report: dict) -> dict:
    checked = {
        key: report[key]
        for key in (
            "documents",
            "checks",
            "categories",
            "outcomes",
            "by_category",
        )
    }
    checked["records_sha256"] = _json_sha256(report["records"])
    return checked


def check_corpus(corpus_path: Path, report: dict) -> bool:
    expected = json.loads(corpus_path.read_text())["expected"]
    if _checked_result(report) != expected:
        raise ValueError("internal scientific checks differ from the frozen corpus")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = evaluate(args.corpus)
    if args.check:
        check_corpus(args.corpus, report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        "internal scientific checks: "
        f"{report['outcomes']['agree']} agree, "
        f"{report['outcomes']['disagree']} disagree, "
        f"{report['outcomes']['tool_refused']} refused"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
