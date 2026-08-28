"""Evaluate numeric-table extraction across controlled scan degradations.

Rows are aligned only by their source label. A missing or structurally ambiguous row
is a tool refusal; an emitted but wrong cell is a disagreement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path

from eval_numeric_tables import _candidate_tables, _resolved_cells, _table_pages
from pdf2md.table_verify import numeric_values_equal, typed_value


_ROOT = Path(__file__).parent.parent
_GROUND_TRUTH = _ROOT / "tests" / "scan_degradation_ground_truth.json"
_SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).translate(_SUBSCRIPTS)
    normalized = re.sub(r"<[^>]+>", "", normalized)
    return re.sub(r"[^a-z0-9]", "", normalized.casefold())


def _page_rows(version_dir: Path) -> dict[int, list[tuple[str, int, list[str]]]]:
    pages = _table_pages(version_dir)
    rows_by_page: dict[int, list[tuple[str, int, list[str]]]] = {}
    for block_id, rows in _candidate_tables(version_dir).items():
        page = pages.get(block_id)
        if page is not None:
            rows_by_page.setdefault(page, []).extend(
                (block_id, row_index, row) for row_index, row in enumerate(rows)
            )
    return rows_by_page


def _source_row(
    rows: list[tuple[str, int, list[str]]], key: str, value_count: int
) -> tuple[str, int, int, list[str]] | None:
    wanted = _row_key(key)
    matches = []
    for block_id, row_index, row in rows:
        for index, cell in enumerate(row):
            if _row_key(str(cell)) == wanted:
                values = [str(value) for value in row[index + 1:index + 1 + value_count]]
                matches.append((block_id, row_index, index, values))
    return matches[0] if len(matches) == 1 and len(matches[0][3]) == value_count else None


def _outcome(actual: str | None, expected: str) -> tuple[str, str | None]:
    if actual is None:
        return "tool_refused", None
    _, numeric_actual, status = typed_value(actual)
    if status == "numeric" and numeric_values_equal(numeric_actual, expected):
        return "agree", numeric_actual
    return "disagree", numeric_actual or None


def _counts(records: list[dict], field: str) -> dict[str, int]:
    return {
        name: sum(record[field] == name for record in records)
        for name in ("agree", "disagree", "tool_refused")
    }


def _ablation_summary(variant_reports: list[dict], records: list[dict]) -> dict:
    by_role = {
        report.get("role"): report
        for report in variant_reports
        if report.get("role") in {"control", "full_combination"}
    }
    full = by_role.get("full_combination")
    clean = by_role.get("control")
    if full is None or clean is None:
        raise ValueError("ablation corpus requires control and full_combination variants")

    by_variant = {}
    for record in records:
        by_variant.setdefault(record["variant"], {})[
            (record["row_key"], record["column"])
        ] = record
    full_cells = by_variant[full["id"]]
    leave_one_out = {}
    for report in variant_reports:
        if report.get("role") != "leave_one_out":
            continue
        removed = report.get("removed_factor")
        if not removed:
            raise ValueError("leave_one_out variant has no removed_factor")
        variant_cells = by_variant[report["id"]]
        recovered = [
            {"row_key": row_key, "column": column}
            for (row_key, column), full_record in full_cells.items()
            if full_record["outcome"] == "tool_refused"
            and variant_cells[(row_key, column)]["outcome"] == "agree"
        ]
        introduced = [
            {"row_key": row_key, "column": column}
            for (row_key, column), full_record in full_cells.items()
            if full_record["outcome"] == "agree"
            and variant_cells[(row_key, column)]["outcome"] != "agree"
        ]
        leave_one_out[removed] = {
            "variant": report["id"],
            "agree_delta": report["agree"] - full["agree"],
            "disagree_delta": report["disagree"] - full["disagree"],
            "tool_refused_delta": report["tool_refused"] - full["tool_refused"],
            "recovered_cells": recovered,
            "recovered_rows": sorted({cell["row_key"] for cell in recovered}),
            "introduced_failures": introduced,
        }
    return {
        "full_variant": full["id"],
        "full": {
            name: full[name] for name in ("agree", "disagree", "tool_refused")
        },
        "clean_control": {
            "variant": clean["id"],
            **{
                name: clean[name]
                for name in ("agree", "disagree", "tool_refused")
            },
        },
        "leave_one_out": leave_one_out,
    }


def evaluate(version_dir: Path, ground_truth: dict, corpus_manifest: dict) -> dict:
    if ground_truth.get("schema_version") != 1 or corpus_manifest.get("schema_version") != 1:
        raise ValueError("unsupported scan-degradation schema_version")
    if ground_truth["source_sha256"] != corpus_manifest["source_sha256"]:
        raise ValueError("ground truth and corpus source hashes differ")

    corpus_pdf = Path(corpus_manifest["manifest_path"]).parent / corpus_manifest["corpus_pdf"]
    if not corpus_pdf.is_file() or _sha256(corpus_pdf) != corpus_manifest["corpus_sha256"]:
        raise ValueError("scan-degradation corpus PDF is missing or has the wrong hash")

    page_rows = _page_rows(version_dir)
    columns = ground_truth["columns"]
    resolved = _resolved_cells(version_dir)
    records = []
    variant_reports = []
    for variant in corpus_manifest["variants"]:
        page = variant["page"]
        rows = page_rows.get(page, [])
        variant_records = []
        for expected_row in ground_truth["rows"]:
            aligned = _source_row(rows, expected_row["key"], len(columns))
            actual_values = aligned[3] if aligned is not None else None
            for column_index, (column, expected) in enumerate(
                zip(columns, expected_row["values"], strict=True)
            ):
                actual = actual_values[column_index] if actual_values is not None else None
                primary_outcome, numeric_actual = _outcome(actual, expected)
                evidence = None
                if aligned is not None:
                    block_id, row_index, key_column, _ = aligned
                    evidence = resolved.get(
                        (block_id, row_index, key_column + 1 + column_index)
                    )
                reader_actual = evidence.get("reader_value") if evidence else None
                best_actual = evidence.get("best_value") if evidence else None
                reader_outcome, _ = _outcome(reader_actual, expected)
                best_outcome, _ = _outcome(best_actual, expected)
                variant_records.append({
                    "variant": variant["id"],
                    "page": page,
                    "row_key": expected_row["key"],
                    "column": column,
                    "expected": expected,
                    "actual": actual,
                    "numeric_actual": numeric_actual,
                    "outcome": primary_outcome,
                    "reader_actual": reader_actual,
                    "reader_outcome": reader_outcome,
                    "best_actual": best_actual,
                    "best_outcome": best_outcome,
                    "confidence": evidence.get("confidence") if evidence else None,
                    "resolution_basis": evidence.get("resolution_basis") if evidence else None,
                })
        counts = _counts(variant_records, "outcome")
        metadata = {
            key: variant[key]
            for key in ("factors", "role", "removed_factor")
            if key in variant
        }
        variant_reports.append({
            "id": variant["id"],
            "page": page,
            "operations": variant["operations"],
            **metadata,
            "checked": len(variant_records),
            **counts,
            "reader": _counts(variant_records, "reader_outcome"),
            "best": _counts(variant_records, "best_outcome"),
        })
        records.extend(variant_records)

    counts = _counts(records, "outcome")
    confidence = {
        name: sum(record["confidence"] == name for record in records)
        for name in ("high", "medium", "low")
    }
    report = {
        "schema_version": 1,
        "method": "controlled_scan_degradation_numeric_cells",
        "contract": {
            "ground_truth": "source-pinned native PDF text checked against source pixels",
            "alignment": "unique normalized row label followed by six fixed-order cells",
            "outcomes": ["agree", "disagree", "tool_refused"],
        },
        "source": ground_truth["source"],
        "source_sha256": ground_truth["source_sha256"],
        "version_dir": str(version_dir),
        "checked": len(records),
        **counts,
        "reader": _counts(records, "reader_outcome"),
        "best": _counts(records, "best_outcome"),
        "confidence": confidence,
        "variants": variant_reports,
        "records": records,
    }
    if corpus_manifest.get("method") == "combined_degradation_leave_one_factor_out":
        report["ablation"] = _ablation_summary(variant_reports, records)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate exact numeric cells across controlled scan degradations."
    )
    parser.add_argument("version_dir", type=Path)
    parser.add_argument("--ground-truth", type=Path, default=_GROUND_TRUTH)
    parser.add_argument("--corpus-manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit nonzero on any disagreement or structural refusal.",
    )
    parser.add_argument("--check", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    corpus_manifest = json.loads(args.corpus_manifest.read_text())
    corpus_manifest["manifest_path"] = str(args.corpus_manifest.resolve())
    report = evaluate(
        args.version_dir,
        json.loads(args.ground_truth.read_text()),
        corpus_manifest,
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"scan degradation: {report['agree']}/{report['checked']} agree, "
        f"{report['disagree']} disagree, {report['tool_refused']} tool-refused"
    )
    for variant in report["variants"]:
        print(
            f"  {variant['id']}: {variant['agree']}/{variant['checked']} agree, "
            f"{variant['disagree']} disagree, {variant['tool_refused']} tool-refused; "
            f"reader {variant['reader']['agree']} agree, "
            f"best {variant['best']['agree']} agree"
        )
    if (args.strict or args.check) and (
        report["disagree"] or report["tool_refused"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
