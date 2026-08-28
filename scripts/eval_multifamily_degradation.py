"""Score source-labelled cells across controlled table degradation families.

Rows are admitted only through a unique source-derived key or an explicitly pinned
row/column position. Missing and ambiguous structure is a refusal, never a guess.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

from eval_numeric_tables import _candidate_tables, _resolved_cells, _table_pages
from pdf2md.tables import gfm_rows


ROOT = Path(__file__).parent.parent
DEFAULT_SOURCES = ROOT / "tests" / "multifamily_degradation_sources.json"
DEFAULT_CORPUS = ROOT / "tests" / "multifamily_degradation_corpus.json"
DEFAULT_RUNTIME = ROOT / "tests" / "multifamily_degradation_runtime.json"

sys.path.insert(0, str(Path(__file__).parent))
from eval_heldout_data_reader import _outcome  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    normalized = re.sub(r"<[^>]+>", "", normalized)
    return re.sub(r"[^a-z0-9.+-]", "", normalized)


def _load_pinned_artifacts(sources_path: Path, sources: dict) -> dict[str, dict]:
    artifacts = {}
    for name, artifact in sources["artifacts"].items():
        path = ROOT / artifact["path"]
        if _sha256(path) != artifact["sha256"]:
            raise ValueError(f"source artifact hash mismatch: {name}")
        artifacts[name] = json.loads(path.read_text())
    return artifacts


def _source_rows(panel: dict) -> list[list[str]]:
    provenance_path = ROOT / panel["version_dir"] / "provenance.json"
    if _sha256(provenance_path) != panel["provenance_sha256"]:
        raise ValueError(f"source provenance hash mismatch: {panel['id']}")
    provenance = json.loads(provenance_path.read_text())
    table = next(
        item for item in provenance["tables"] if item["block_id"] == panel["block_id"]
    )
    return gfm_rows(table["gfm"])


def _heldout_ground_truth(
    family: dict,
    labels: dict,
    source_panels: dict[str, dict],
) -> list[dict]:
    labelled = next(
        panel for panel in labels["panels"] if panel["id"] == family["heldout_panel"]
    )
    panel = source_panels.get(labelled["id"], labelled.get("source_panel"))
    if panel is None:
        raise ValueError(f"source panel unavailable: {labelled['id']}")
    rows = _source_rows(panel)
    offset = int(labelled["structured_row_offset"])
    key_column = int(family["key_column"])
    records = []
    for index, cell in enumerate(labelled["cells"]):
        row_index = offset + int(cell["row_position"])
        row = rows[row_index]
        column = int(cell["column"])
        expected_kind = cell.get("expected_kind", "numeric")
        if _outcome(row[column], None, cell["expected"], expected_kind) != "agree":
            raise ValueError(
                f"source value does not match label: {family['id']} cell {index}"
            )
        records.append({
            "id": f"{family['id']}:{index}",
            "family": family["id"],
            "locator": "key",
            "row_key": row[key_column],
            "key_column": key_column,
            "column_offset": column - key_column,
            "expected": cell["expected"],
            "expected_kind": expected_kind,
            "class": cell["class"],
        })
    return records


def _indexed_ground_truth(family: dict, labels: dict) -> list[dict]:
    provenance_path = ROOT / family["version_dir"] / "provenance.json"
    if _sha256(provenance_path) != family["provenance_sha256"]:
        raise ValueError(f"source provenance hash mismatch: {family['id']}")
    provenance = json.loads(provenance_path.read_text())
    table = next(
        item
        for item in provenance["tables"]
        if item["block_id"] == family["source_block"]
    )
    rows = gfm_rows(table["gfm"])
    cells = [
        cell
        for document in labels["documents"]
        for cell in document["cells"]
        if cell["block_id"] == family["label_block"]
    ]
    records = []
    for index, cell in enumerate(cells):
        row = int(cell["row"])
        column = int(cell["column"])
        if _outcome(rows[row][column], None, cell["expected"]) != "agree":
            raise ValueError(
                f"source value does not match label: {family['id']} cell {index}"
            )
        records.append({
            "id": f"{family['id']}:{index}",
            "family": family["id"],
            "locator": "index",
            "row": row,
            "column": column,
            "expected": cell["expected"],
            "expected_kind": "numeric",
            "class": "leading_dot_decimal",
        })
    return records


def _ground_truth(sources_path: Path, sources: dict) -> dict[str, list[dict]]:
    artifacts = _load_pinned_artifacts(sources_path, sources)
    heldout = artifacts["heldout_labels"]
    source_panels = {
        panel["id"]: panel for panel in artifacts["source_row_corpus"]["panels"]
    }
    ground_truth = {}
    for family in sources["families"]:
        if family["row_locator"] == "key":
            records = _heldout_ground_truth(family, heldout, source_panels)
        elif family["row_locator"] == "index":
            records = _indexed_ground_truth(family, artifacts[family["label_artifact"]])
        else:
            raise ValueError(f"unsupported row locator: {family['row_locator']}")
        ground_truth[family["id"]] = records
    return ground_truth


def _page_tables(version_dir: Path) -> dict[int, list[tuple[str, list[list[str]]]]]:
    pages = _table_pages(version_dir)
    by_page: dict[int, list[tuple[str, list[list[str]]]]] = {}
    for block_id, rows in _candidate_tables(version_dir).items():
        page = pages.get(block_id)
        if page is not None:
            by_page.setdefault(page, []).append((block_id, rows))
    return by_page


def _locate_key_cell(
    tables: list[tuple[str, list[list[str]]]],
    row_key: str,
    key_column: int,
    column_offset: int,
) -> tuple[str, int, int, str] | None:
    wanted = _row_key(row_key)
    matches = []
    for block_id, rows in tables:
        for row_index, row in enumerate(rows):
            if key_column >= len(row) or _row_key(row[key_column]) != wanted:
                continue
            target_column = key_column + column_offset
            if 0 <= target_column < len(row):
                matches.append(
                    (block_id, row_index, target_column, row[target_column])
                )
    return matches[0] if len(matches) == 1 else None


def _locate_index_cell(
    tables: list[tuple[str, list[list[str]]]], row: int, column: int
) -> tuple[str, int, int, str] | None:
    matches = [
        (block_id, row, column, rows[row][column])
        for block_id, rows in tables
        if row < len(rows) and column < len(rows[row])
    ]
    return matches[0] if len(matches) == 1 else None


def _counts(records: list[dict], field: str) -> dict[str, int]:
    counter = Counter(record[field] for record in records)
    return {
        "checked": len(records),
        "agree": counter["agree"],
        "disagree": counter["disagree"],
        "tool_refused": counter["tool_refused"],
    }


def _group(records: list[dict], key: str) -> dict[str, dict]:
    if key == "factors":
        values = sorted({value for record in records for value in record[key]})
    else:
        values = sorted({record[key] for record in records})
    grouped = {}
    for value in values:
        if key == "factors":
            selected = [record for record in records if value in record[key]]
        else:
            selected = [record for record in records if record[key] == value]
        grouped[value] = {
            "primary": _counts(selected, "primary_outcome"),
            "reader": _counts(selected, "reader_outcome"),
            "best": _counts(selected, "best_outcome"),
        }
    return grouped


def _clean_transitions(records: list[dict]) -> dict[str, dict]:
    by_cell = {
        (record["family"], record["variant"], record["cell_id"]): record
        for record in records
    }
    transitions = {}
    for family, variant in sorted({(r["family"], r["variant"]) for r in records}):
        if variant == "clean":
            continue
        selected = [r for r in records if r["family"] == family and r["variant"] == variant]
        counter = Counter()
        for record in selected:
            clean = by_cell[(family, "clean", record["cell_id"])]
            counter[f"{clean['primary_outcome']}_to_{record['primary_outcome']}"] += 1
        transitions[f"{family}:{variant}"] = dict(sorted(counter.items()))
    return transitions


def evaluate(
    version_dir: Path,
    sources_path: Path,
    corpus_manifest_path: Path,
    runtime_path: Path = DEFAULT_RUNTIME,
) -> dict:
    sources = json.loads(sources_path.read_text())
    manifest = json.loads(corpus_manifest_path.read_text())
    runtime = json.loads(runtime_path.read_text())
    if sources.get("schema_version") != 1 or manifest.get("schema_version") != 1:
        raise ValueError("unsupported multifamily degradation schema_version")
    if runtime.get("schema_version") != 1:
        raise ValueError("unsupported multifamily degradation runtime schema_version")
    if manifest["sources_sha256"] != _sha256(sources_path):
        raise ValueError("corpus manifest source definition hash mismatch")
    corpus_pdf = corpus_manifest_path.parent / manifest["corpus_pdf"]
    if _sha256(corpus_pdf) != manifest["corpus_sha256"]:
        raise ValueError("multifamily degradation PDF hash mismatch")

    ground_truth = _ground_truth(sources_path, sources)
    page_tables = _page_tables(version_dir)
    evidence = _resolved_cells(version_dir)
    records = []
    for page in manifest["pages"]:
        family = page["family"]
        tables = page_tables.get(int(page["page"]), [])
        for expected in ground_truth[family]:
            if expected["locator"] == "key":
                located = _locate_key_cell(
                    tables,
                    expected["row_key"],
                    expected["key_column"],
                    expected["column_offset"],
                )
            else:
                located = _locate_index_cell(
                    tables, expected["row"], expected["column"]
                )
            actual = located[3] if located is not None else None
            primary_outcome = _outcome(
                actual,
                "structure_unavailable" if located is None else None,
                expected["expected"],
                expected["expected_kind"],
            )
            cell_evidence = evidence.get(located[:3]) if located is not None else None
            reader_actual = cell_evidence.get("reader_value") if cell_evidence else None
            best_actual = cell_evidence.get("best_value") if cell_evidence else None
            records.append({
                "cell_id": expected["id"],
                "family": family,
                "typography": next(
                    item["typography"]
                    for item in sources["families"]
                    if item["id"] == family
                ),
                "variant": page["variant"],
                "factors": page["factors"],
                "role": page["role"],
                "page": page["page"],
                "class": expected["class"],
                "expected": expected["expected"],
                "expected_kind": expected["expected_kind"],
                "actual": actual,
                "primary_outcome": primary_outcome,
                "reader_actual": reader_actual,
                "reader_outcome": _outcome(
                    reader_actual,
                    None if reader_actual is not None else "reader_unavailable",
                    expected["expected"],
                    expected["expected_kind"],
                ),
                "best_actual": best_actual,
                "best_outcome": _outcome(
                    best_actual,
                    None if best_actual is not None else "best_value_unavailable",
                    expected["expected"],
                    expected["expected_kind"],
                ),
                "confidence": cell_evidence.get("confidence") if cell_evidence else None,
                "resolution_basis": (
                    cell_evidence.get("resolution_basis") if cell_evidence else None
                ),
                "source_block_id": located[0] if located is not None else None,
                "source_row": located[1] if located is not None else None,
                "source_column": located[2] if located is not None else None,
            })

    provenance_path = version_dir / "provenance.json"
    report = {
        "schema_version": 1,
        "method": "controlled_multifamily_table_degradation",
        "contract": {
            "ground_truth": "source-checked cells fixed before degradation",
            "alignment": "unique source-derived row key or explicitly pinned index",
            "outcomes": ["agree", "disagree", "tool_refused"],
            "optimization_target": "minimize emitted wrong values; do not hide them in refusals",
        },
        "version_dir": str(version_dir),
        "provenance_sha256": _sha256(provenance_path),
        "sources": str(sources_path),
        "sources_sha256": _sha256(sources_path),
        "corpus_manifest": str(corpus_manifest_path),
        "corpus_manifest_sha256": _sha256(corpus_manifest_path),
        "corpus_sha256": manifest["corpus_sha256"],
        "runtime": runtime,
        "runtime_sha256": _sha256(runtime_path),
        "families": len(ground_truth),
        "variants": len(manifest["variants"]),
        "labelled_cells_per_variant": sum(map(len, ground_truth.values())),
        "primary": _counts(records, "primary_outcome"),
        "reader": _counts(records, "reader_outcome"),
        "best": _counts(records, "best_outcome"),
        "by_family": _group(records, "family"),
        "by_variant": _group(records, "variant"),
        "by_factor": _group(records, "factors"),
        "by_role": _group(records, "role"),
        "clean_transitions": _clean_transitions(records),
        "records": records,
    }
    return report


def _checked_result(report: dict) -> dict:
    return {
        key: report[key]
        for key in (
            "provenance_sha256",
            "sources_sha256",
            "corpus_manifest_sha256",
            "corpus_sha256",
            "runtime_sha256",
            "families",
            "variants",
            "labelled_cells_per_variant",
            "primary",
            "reader",
            "best",
            "by_family",
            "by_variant",
            "by_factor",
            "by_role",
            "clean_transitions",
        )
    }


def check_corpus(corpus_path: Path, report: dict) -> bool:
    corpus = json.loads(corpus_path.read_text())
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported multifamily degradation corpus schema_version")
    for name, artifact in corpus["artifacts"].items():
        if _sha256(ROOT / artifact["path"]) != artifact["sha256"]:
            raise ValueError(f"multifamily degradation artifact hash mismatch: {name}")
    return _checked_result(report) == corpus["expected"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score source-labelled cells across table degradation families."
    )
    parser.add_argument("version_dir", type=Path)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--corpus-manifest", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    report = evaluate(
        args.version_dir,
        args.sources,
        args.corpus_manifest,
        args.runtime,
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("primary", "reader", "best")}, indent=2))
    if args.check and not check_corpus(args.corpus, report):
        raise SystemExit("multifamily degradation corpus differs from expected results")


if __name__ == "__main__":
    main()
