"""Score the table row/grid audit against labelled structural truth.

    uv run python scripts/eval_table_audit.py OUT_DIR [--labels FILE]
                                              [--json OUT] [--check]

tests/table_audit_labels.json records, per table, which structural findings are
true of it — established from the source page's own text and from two
independent line-finding mechanisms agreeing, never from running this audit.
The harness replays `table_audit.audit_table` over each labelled table and
reports precision and recall per finding kind.

Precision is the number that matters. A false "row dropped" on a correct table
costs more than a missed one: the whole point of the finding is that a reader
can trust it enough to go and check. `--check` fails when a labelled table
gains a finding it shouldn't have, or loses one it should.

Reads completed bundles; it does not reconvert. Missing sources and hash
mismatches fail loudly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from pdf2md.engine_state import load_engine_state
from pdf2md.enrich import GlyphIndex
from pdf2md.table_audit import audit_table
from pdf2md.tables import gfm_rows, html_tables

_ROOT = Path(__file__).parent.parent
_DEFAULT_LABELS = _ROOT / "tests" / "table_audit_labels.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _find_bundle(out_dir: Path, source_sha256: str, required: set[str]) -> Path:
    """The newest bundle of this source that carries the labelled tables.

    Newest-overall is not enough: block ids are the engine's, so a MinerU
    conversion of the same PDF has `#/mineru/...` where the labels say
    `#/tables/0`. Selecting by content rather than by version number keeps the
    harness working whatever else has been converted alongside."""
    for doc_dir in sorted(out_dir.iterdir()):
        source = doc_dir / "source.pdf"
        if not source.is_file() or _sha256(source) != source_sha256:
            continue
        versions = sorted(
            (v for v in doc_dir.glob("v*") if (v / "provenance.json").is_file()),
            key=lambda v: int(v.name[1:]),
            reverse=True,
        )
        for version in versions:
            ids = {
                table["block_id"]
                for table in json.loads((version / "provenance.json").read_text())["tables"]
            }
            if required <= ids:
                return version
    raise SystemExit(
        f"no bundle under {out_dir} for source {source_sha256} carrying "
        f"{sorted(required)}"
    )


def _rendered_rows(table) -> list[list[str]]:
    if (table.gfm or "").strip():
        return gfm_rows(table.gfm)
    tables = html_tables(table.html) if table.html else []
    return tables[0] if tables else []


def score(out_dir: Path, labels: dict) -> dict:
    per_kind: dict[str, Counter[str]] = {}
    rows: list[dict] = []
    for document in labels["documents"]:
        version_dir = _find_bundle(
            out_dir,
            document["source_sha256"],
            {label["block_id"] for label in document["tables"]},
        )
        result = load_engine_state(version_dir)
        by_id = {t.block_id: t for t in result.tables}
        with GlyphIndex(version_dir.parent / "source.pdf") as glyphs:
            for label in document["tables"]:
                table = by_id.get(label["block_id"])
                if table is None:
                    raise SystemExit(
                        f"{document['source']}: {label['block_id']} not in {version_dir}"
                    )
                grid = _rendered_rows(table)
                payload = audit_table(
                    grid[0] if grid else [],
                    grid[1:],
                    result.raw_tables.get(table.block_id),
                    glyphs.page_chars(table.page),
                    table.bbox,
                )
                found = {f["kind"] for f in payload.get("findings", [])}
                expected = set(label["expect"])
                for kind in found | expected:
                    counts = per_kind.setdefault(kind, Counter())
                    if kind in found and kind in expected:
                        counts["true_positive"] += 1
                    elif kind in found:
                        counts["false_positive"] += 1
                    else:
                        counts["false_negative"] += 1
                rows.append({
                    "source": document["source"],
                    "version": version_dir.name,
                    "block_id": label["block_id"],
                    "page": label["page"],
                    "expected": sorted(expected),
                    "found": sorted(found),
                    "missed": sorted(expected - found),
                    "spurious": sorted(found - expected),
                    "refusal_expected": label.get("refusal"),
                    "refusal": payload.get("rows_refusal"),
                })
    return {"tables": rows, "per_kind": {k: dict(v) for k, v in per_kind.items()}}


def _rate(counts: dict[str, int], hit: str, miss: str) -> float | None:
    total = counts.get(hit, 0) + counts.get(miss, 0)
    return counts.get(hit, 0) / total if total else None


def report(result: dict) -> list[str]:
    problems: list[str] = []
    print(f"{'TABLE':44s} {'EXPECTED':10s} {'FOUND':8s} NOTE")
    print("-" * 92)
    for row in result["tables"]:
        note = ""
        if row["spurious"]:
            note = "SPURIOUS: " + ", ".join(row["spurious"])
            problems.append(f"{row['source']} {row['block_id']}: {note}")
        if row["missed"]:
            note = (note + "  " if note else "") + "MISSED: " + ", ".join(row["missed"])
            problems.append(
                f"{row['source']} {row['block_id']}: MISSED {', '.join(row['missed'])}"
            )
        if row["refusal_expected"] and row["refusal"] != row["refusal_expected"]:
            note = (note + "  " if note else "") + (
                f"REFUSAL: {row['refusal']} != {row['refusal_expected']}"
            )
            problems.append(
                f"{row['source']} {row['block_id']}: refusal {row['refusal']!r} "
                f"!= {row['refusal_expected']!r}"
            )
        label = f"{row['source']} {row['block_id']} p{row['page']}"
        print(f"{label:44s} {len(row['expected']):<10d} {len(row['found']):<8d} {note}")

    print()
    print(f"{'FINDING':24s} {'TP':>4s} {'FP':>4s} {'FN':>4s} {'PRECISION':>10s} {'RECALL':>8s}")
    print("-" * 60)
    for kind, counts in sorted(result["per_kind"].items()):
        precision = _rate(counts, "true_positive", "false_positive")
        recall = _rate(counts, "true_positive", "false_negative")
        print(
            f"{kind:24s} {counts.get('true_positive', 0):>4d} "
            f"{counts.get('false_positive', 0):>4d} {counts.get('false_negative', 0):>4d} "
            f"{'-' if precision is None else f'{precision:>10.2f}'} "
            f"{'-' if recall is None else f'{recall:>8.2f}'}"
        )
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path, help="directory of completed bundles")
    parser.add_argument("--labels", type=Path, default=_DEFAULT_LABELS)
    parser.add_argument("--json", type=Path, default=None, help="write the full report here")
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero on any spurious or missed finding")
    args = parser.parse_args()

    result = score(args.out_dir, json.loads(args.labels.read_text()))
    problems = report(result)
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + "\n")
    if problems:
        print("\nPROBLEMS:")
        for problem in problems:
            print(f"  - {problem}")
    if args.check and problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
