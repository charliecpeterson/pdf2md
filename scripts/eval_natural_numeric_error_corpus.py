"""Evaluate source-checked numeric evidence across non-Fischer documents.

Value errors, auxiliary-reader outcomes, and structural omissions remain separate so
an exact surviving digit string cannot hide a missing column or guessed table shape.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from eval_numeric_tables import _values_equal, evaluate as evaluate_labels


ROOT = Path(__file__).parent.parent
DEFAULT_CORPUS = ROOT / "tests" / "natural_numeric_error_corpus.json"
OUTCOMES = ("checked", "agree", "disagree", "tool_refused")
STRUCTURE_OUTCOMES = (
    "row_mapping_refusals",
    "missing_numeric_cells",
    "structured_table_refusals",
    "auxiliary_geometry_refusals",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _counts(source: dict) -> dict[str, int]:
    return {key: int(source[key]) for key in OUTCOMES}


def _add_counts(*counts: dict[str, int]) -> dict[str, int]:
    return {key: sum(item[key] for item in counts) for key in OUTCOMES}


def _reader_counts(records: list[dict]) -> dict[str, int]:
    outcomes = [record["outcome"] for record in records]
    return {
        "checked": len(outcomes),
        "agree": outcomes.count("agree"),
        "disagree": outcomes.count("disagree"),
        "tool_refused": outcomes.count("tool_refused"),
    }


def _reference_counts(records: list[dict]) -> dict[str, int]:
    outcomes = [record["reference_outcome"] for record in records]
    return {
        "checked": len(outcomes),
        "agree": outcomes.count("agree"),
        "disagree": outcomes.count("disagree"),
        "tool_refused": outcomes.count("tool_refused"),
    }


def _rates(primary: dict[str, int], reader: dict[str, int]) -> dict[str, float | None]:
    return {
        "primary_error_rate": (
            primary["disagree"] / primary["checked"] if primary["checked"] else None
        ),
        "reader_disagreement_rate": (
            reader["disagree"] / reader["checked"] if reader["checked"] else None
        ),
        "reader_refusal_rate": (
            reader["tool_refused"] / reader["checked"] if reader["checked"] else None
        ),
    }


def _load_artifacts(root: Path, corpus: dict) -> dict[str, dict]:
    loaded = {}
    for name, artifact in corpus["artifacts"].items():
        path = root / artifact["path"]
        if _sha256(path) != artifact["sha256"]:
            raise ValueError(f"natural numeric artifact hash mismatch: {name}")
        if path.suffix == ".json":
            loaded[name] = json.loads(path.read_text())
    return loaded


def _manual_case(name: str, review: dict, artifacts: dict[str, dict]) -> dict:
    report = artifacts[review["report"]]
    if int(report["checked"]) != review["primary"]["checked"]:
        raise ValueError(f"manual review no longer covers every extracted cell: {name}")
    primary = review["primary"]
    reader = _counts(report)
    case = {
        "id": name,
        "role": review["role"],
        "table_family": review["table_family"],
        "typography": review["typography"],
        "documents_examined": review["documents_examined"],
        "documents_with_reviewed_cells": review["documents_with_reviewed_cells"],
        "primary": primary,
        "reader": reader,
        "structure": review["structure"],
    }
    case["rates"] = _rates(primary, reader)
    case["documents"] = [{
        "id": name,
        "source_sha256": report["source_sha256"],
        "primary": primary,
        "reader": reader,
        "structure": review["structure"],
        "rates": case["rates"],
    }]
    return case


def _pdf_parse_case(artifacts: dict[str, dict]) -> dict:
    ground = artifacts["pdf_parse_ground_truth"]
    evidence = artifacts["pdf_parse_evidence"]
    if ground["checked"] != evidence["checked"]:
        raise ValueError("pdf-parse-bench ground truth and reader evidence diverged")
    evidence_by_source = {
        document["source_sha256"]: [
            record
            for record in evidence["records"]
            if record["source_sha256"] == document["source_sha256"]
        ]
        for document in ground["documents"]
    }
    documents = []
    for document in ground["documents"]:
        primary = {
            "checked": document["checked_cells"],
            "agree": document["checked_cells"] - document["disagree"],
            "disagree": document["disagree"],
            "tool_refused": 0,
        }
        reader = _reference_counts(evidence_by_source[document["source_sha256"]])
        structure = {
            "row_mapping_refusals": document["row_refusals"],
            "missing_numeric_cells": 0,
            "structured_table_refusals": 0,
        }
        documents.append({
            "id": document["document_id"],
            "source_sha256": document["source_sha256"],
            "primary": primary,
            "reader": reader,
            "structure": structure,
            "rates": _rates(primary, reader),
        })
    primary = {
        "checked": ground["checked"],
        "agree": ground["agree"],
        "disagree": ground["disagree"],
        "tool_refused": 0,
    }
    reader = {
        "checked": ground["checked"],
        **{
            key: int(evidence["reference"][key])
            for key in ("agree", "disagree", "tool_refused")
        },
    }
    case = {
        "id": "pdf_parse_bench",
        "role": "independent_reference_accepted_rows",
        "table_family": "born_digital_latex_ground_truth",
        "typography": "mixed_born_digital",
        "documents_examined": len(ground["documents"]),
        "documents_with_reviewed_cells": sum(
            document["checked_cells"] > 0 for document in ground["documents"]
        ),
        "primary": primary,
        "reader": reader,
        "structure": {
            "row_mapping_refusals": sum(
                document["row_refusals"] for document in ground["documents"]
            ),
            "missing_numeric_cells": 0,
            "structured_table_refusals": 0,
        },
        "documents": documents,
    }
    case["rates"] = _rates(primary, reader)
    return case


def _grasp_case(artifacts: dict[str, dict]) -> dict:
    report = artifacts["grasp_report"]
    labels = artifacts["grasp_labels"]
    panel_ids = [panel["id"] for panel in labels["panels"] if panel["id"].startswith("grasp-")]
    panel_results = [report["panel_results"][panel_id] for panel_id in panel_ids]
    primary = _add_counts(*(panel["primary"] for panel in panel_results))
    labelled = sum(
        len(panel["cells"])
        for panel in labels["panels"]
        if panel["id"] in panel_ids
    )
    if primary["checked"] != labelled:
        raise ValueError("GRASP held-out labels and report diverged")
    reader = _add_counts(*(panel["tesseract"] for panel in panel_results))
    structure = {
        "row_mapping_refusals": 0,
        "missing_numeric_cells": 0,
        "structured_table_refusals": 0,
    }
    case = {
        "id": "grasp",
        "role": "targeted_syntax_and_geometry_coverage",
        "table_family": "born_digital_fixed_width_scientific",
        "typography": "monospace_and_serif_mixed_precision",
        "documents_examined": 1,
        "documents_with_reviewed_cells": 1,
        "primary": primary,
        "reader": reader,
        "structure": structure,
    }
    case["rates"] = _rates(primary, reader)
    case["documents"] = [{
        "id": "grasp2018-manual",
        "source_sha256": "bf0e2756389dfaa7fcec6bb67b5217d8aa4e9902b969e44234e5a7255c037687",
        "primary": primary,
        "reader": reader,
        "structure": structure,
        "rates": case["rates"],
    }]
    return case


def _label_keys(*labels: dict) -> set[tuple[str, int, int]]:
    return {
        (cell["block_id"], int(cell["row"]), int(cell["column"]))
        for label_set in labels
        for document in label_set["documents"]
        for cell in document["cells"]
    }


def _slater_case(root: Path, artifacts: dict[str, dict]) -> dict:
    controls = artifacts["slater_controls"]
    natural_controls = artifacts["slater_natural_controls"]
    errors = artifacts["slater_errors"]
    primary = _add_counts(
        _counts(evaluate_labels(root / "out/agent-full-books", controls)),
        _counts(evaluate_labels(root / "out/agent-full-books", natural_controls)),
        _counts(evaluate_labels(root / "out/agent-full-books", errors)),
    )
    keys = _label_keys(controls, natural_controls, errors)
    records = [
        record
        for record in artifacts["slater_report"]["records"]
        if (record["block_id"], int(record["row"]), int(record["column"])) in keys
    ]
    if len(records) != primary["checked"]:
        raise ValueError("Slater labelled cells and reader report diverged")
    reader = _reader_counts(records)
    structure = {
        "row_mapping_refusals": 0,
        "missing_numeric_cells": 0,
        "structured_table_refusals": 0,
    }
    case = {
        "id": "slater",
        "role": "error_enriched_source_pixel_review",
        "table_family": "scanned_book_mixed_numeric",
        "typography": "degraded_serif_fixed_precision",
        "documents_examined": 1,
        "documents_with_reviewed_cells": 1,
        "primary": primary,
        "reader": reader,
        "structure": structure,
    }
    case["rates"] = _rates(primary, reader)
    case["documents"] = [{
        "id": "slater-atomic-structure-vol1",
        "source_sha256": controls["documents"][0]["source_sha256"],
        "primary": primary,
        "reader": reader,
        "structure": structure,
        "rates": case["rates"],
    }]
    return case


def _labelled_scan_case(
    root: Path,
    name: str,
    review: dict,
    artifacts: dict[str, dict],
) -> dict:
    primary_parts = []
    reader_parts = []
    source_hashes = set()
    for label_set in review["label_sets"]:
        labels = artifacts[label_set["labels"]]
        report = artifacts[label_set["report"]]
        primary = _counts(evaluate_labels(root / review["out_dir"], labels))
        if primary != _counts(report):
            raise ValueError(f"{name} labels and primary report diverged")
        if len(report["records"]) != primary["checked"]:
            raise ValueError(f"{name} labels and reader records diverged")
        primary_parts.append(primary)
        reader_parts.append(_reference_counts(report["records"]))
        source_hashes.update(
            document["source_sha256"] for document in labels["documents"]
        )
    if len(source_hashes) != 1:
        raise ValueError(f"{name} must describe exactly one source document")

    primary = _add_counts(*primary_parts)
    reader = _add_counts(*reader_parts)
    structure = review["structure"]
    case = {
        "id": name,
        "role": review["role"],
        "table_family": review["table_family"],
        "typography": review["typography"],
        "documents_examined": 1,
        "documents_with_reviewed_cells": 1,
        "primary": primary,
        "reader": reader,
        "structure": structure,
    }
    case["rates"] = _rates(primary, reader)
    case["documents"] = [{
        "id": review["document_id"],
        "source_sha256": source_hashes.pop(),
        "primary": primary,
        "reader": reader,
        "structure": structure,
        "rates": case["rates"],
    }]
    return case


def _nist_geometry_case(root: Path, review: dict, artifacts: dict[str, dict]) -> dict:
    labels = artifacts[review["labels"]]
    report = artifacts[review["report"]]
    primary = _counts(evaluate_labels(root / review["out_dir"], labels))
    keys = _label_keys(labels)
    records = [
        record
        for record in report["records"]
        if (record["block_id"], int(record["row"]), int(record["column"])) in keys
    ]
    if len(records) != primary["checked"]:
        raise ValueError("NIST labels and discovery report diverged")
    if any(record["outcome"] != "tool_refused" for record in records):
        raise ValueError("NIST labelled controls no longer have safe reader refusals")
    raw_reader = {
        key: int(report[key])
        for key in ("checked", "agree", "disagree", "tool_refused")
    }
    if raw_reader != review["raw_reader"]:
        raise ValueError("NIST full-table reader geometry baseline drifted")

    reader = _reader_counts(records)
    structure = review["structure"]
    case = {
        "id": "nist_nitrogen",
        "role": review["role"],
        "table_family": review["table_family"],
        "typography": review["typography"],
        "documents_examined": 1,
        "documents_with_reviewed_cells": 1,
        "primary": primary,
        "reader": reader,
        "structure": structure,
        "raw_reader": raw_reader,
        "geometry_audit": review["geometry_audit"],
    }
    case["rates"] = _rates(primary, reader)
    case["documents"] = [{
        "id": review["document_id"],
        "source_sha256": labels["documents"][0]["source_sha256"],
        "primary": primary,
        "reader": reader,
        "structure": structure,
        "rates": case["rates"],
    }]
    return case


def _cell_records(
    root: Path,
    corpus: dict,
    artifacts: dict[str, dict],
    cases: list[dict],
) -> list[dict]:
    case_by_id = {case["id"]: case for case in cases}
    records = []

    def add(
        case_id: str,
        document: str,
        source_sha256: str,
        record: dict,
        primary_outcome: str,
        reader_outcome: str,
        primary_value: str | None,
        reader_value: str | None,
        reader_refusal: str | None = None,
        confidence: str | None = None,
        resolution_basis: str | None = None,
        reader_geometry: str = "available",
    ) -> None:
        case = case_by_id[case_id]
        records.append({
            "id": (
                f"{source_sha256}:{record['block_id']}:"
                f"{int(record['row'])}:{int(record['column'])}"
            ),
            "case": case_id,
            "document": document,
            "source_sha256": source_sha256,
            "role": case["role"],
            "table_family": case["table_family"],
            "typography": case["typography"],
            "page": int(record["page"]),
            "block_id": record["block_id"],
            "row": int(record["row"]),
            "column": int(record["column"]),
            "primary_value": primary_value,
            "primary_outcome": primary_outcome,
            "reader_value": reader_value,
            "reader_outcome": reader_outcome,
            "readers_agree": (
                primary_value is not None
                and reader_value is not None
                and _values_equal(primary_value, reader_value)
            ),
            "reader_refusal": reader_refusal,
            "reader_geometry": reader_geometry,
            "confidence": confidence,
            "resolution_basis": resolution_basis,
        })

    ground = artifacts["pdf_parse_ground_truth"]
    document_by_source = {
        document["source_sha256"]: document["document_id"]
        for document in ground["documents"]
    }
    for record in artifacts["pdf_parse_evidence"]["records"]:
        add(
            "pdf_parse_bench",
            document_by_source[record["source_sha256"]],
            record["source_sha256"],
            record,
            record["outcome"],
            record["reference_outcome"],
            record.get("actual"),
            record.get("reference_actual"),
            record.get("reference_refusal_reason"),
            record.get("confidence"),
            record.get("resolution_basis"),
        )

    for case_id, review in corpus["manual_reviews"].items():
        report = artifacts[review["report"]]
        if review["primary"]["disagree"] or review["primary"]["tool_refused"]:
            raise ValueError(f"manual per-cell outcomes unavailable: {case_id}")
        for record in report["records"]:
            add(
                case_id,
                case_id,
                report["source_sha256"],
                record,
                "agree",
                record["outcome"],
                record.get("primary_value"),
                record.get("reader_value"),
                record.get("refusal_reason"),
                reader_geometry=(
                    "refused"
                    if record.get("refusal_reason") == "grid_alignment_failed"
                    else "available"
                ),
            )

    for record in artifacts["grasp_manifest"]["records"]:
        if not record["panel_id"].startswith("grasp-"):
            continue
        primary_outcome = (
            "agree"
            if _values_equal(record["primary"], record["expected"])
            else "disagree"
        )
        reader_outcome = (
            "agree"
            if _values_equal(record["tesseract"], record["expected"])
            else "disagree"
        )
        add(
            "grasp",
            "grasp2018-manual",
            record["source_sha256"],
            record | {"row": record["row_position"]},
            primary_outcome,
            reader_outcome,
            record["primary"],
            record["tesseract"],
        )

    slater_labels = [
        artifacts[name]
        for name in ("slater_controls", "slater_natural_controls", "slater_errors")
    ]
    slater_reader = {
        (record["block_id"], int(record["row"]), int(record["column"])): record
        for record in artifacts["slater_report"]["records"]
    }
    for labels in slater_labels:
        for primary in evaluate_labels(root / "out/agent-full-books", labels)["records"]:
            key = (primary["block_id"], primary["row"], primary["column"])
            reader = slater_reader[key]
            add(
                "slater",
                "slater-atomic-structure-vol1",
                primary["source_sha256"],
                primary,
                primary["outcome"],
                reader["outcome"],
                primary.get("actual"),
                reader.get("reader_value"),
                reader.get("refusal_reason"),
            )

    for case_id, review in corpus.get("labelled_scans", {}).items():
        for label_set in review["label_sets"]:
            report = artifacts[label_set["report"]]
            for record in report["records"]:
                add(
                    case_id,
                    review["document_id"],
                    record["source_sha256"],
                    record,
                    record["outcome"],
                    record["reference_outcome"],
                    record.get("actual"),
                    record.get("reference_actual"),
                    record.get("reference_refusal_reason"),
                    record.get("confidence"),
                    record.get("resolution_basis"),
                    reader_geometry=(
                        "refused"
                        if record.get("reference_refusal_reason")
                        == "grid_alignment_failed"
                        else "available"
                    ),
                )

    if "nist_geometry_review" in corpus:
        review = corpus["nist_geometry_review"]
        labels = artifacts[review["labels"]]
        reader_by_key = {
            (record["block_id"], int(record["row"]), int(record["column"])): record
            for record in artifacts[review["report"]]["records"]
        }
        for primary in evaluate_labels(root / review["out_dir"], labels)["records"]:
            key = (primary["block_id"], primary["row"], primary["column"])
            reader = reader_by_key[key]
            add(
                "nist_nitrogen",
                review["document_id"],
                primary["source_sha256"],
                primary,
                primary["outcome"],
                "tool_refused",
                primary.get("actual"),
                None,
                reader.get("refusal_reason") or "unsafe_auxiliary_geometry",
                reader_geometry="refused",
            )

    if len({record["id"] for record in records}) != len(records):
        raise ValueError("natural numeric per-cell evidence contains duplicate cells")
    return records


def evaluate(root: Path, corpus: dict) -> dict:
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported natural numeric corpus schema_version")
    artifacts = _load_artifacts(root, corpus)
    cases = [_pdf_parse_case(artifacts)]
    cases.extend(
        _manual_case(name, review, artifacts)
        for name, review in corpus["manual_reviews"].items()
    )
    cases.extend((_grasp_case(artifacts), _slater_case(root, artifacts)))
    cases.extend(
        _labelled_scan_case(root, name, review, artifacts)
        for name, review in corpus.get("labelled_scans", {}).items()
    )
    if "nist_geometry_review" in corpus:
        cases.append(
            _nist_geometry_case(root, corpus["nist_geometry_review"], artifacts)
        )
    primary = _add_counts(*(case["primary"] for case in cases))
    reader = _add_counts(*(case["reader"] for case in cases))
    structure = {
        key: sum(case["structure"].get(key, 0) for case in cases)
        for key in STRUCTURE_OUTCOMES
    }
    records = _cell_records(root, corpus, artifacts, cases)
    record_primary = _reader_counts([
        {"outcome": record["primary_outcome"]} for record in records
    ])
    record_reader = _reader_counts([
        {"outcome": record["reader_outcome"]} for record in records
    ])
    if record_primary != primary or record_reader != reader:
        raise ValueError("natural numeric per-cell evidence diverged from aggregate")
    return {
        "schema_version": 1,
        "method": "non_fischer_natural_numeric_error_corpus",
        "contract": {
            "value_outcomes": "source-checked extracted numeric cells",
            "reader_outcomes": "independent Tesseract evidence only",
            "structure": "row refusals, omitted numeric cells, table-shape refusals, and unsafe auxiliary geometry are not counted as correct values",
            "sampling": "roles remain separate; the pooled result is not a prevalence estimate",
        },
        "documents_examined": sum(case["documents_examined"] for case in cases),
        "documents_with_reviewed_cells": sum(
            case["documents_with_reviewed_cells"] for case in cases
        ),
        "cells": primary["checked"],
        "primary": primary,
        "reader": reader,
        "structure": structure,
        "cases": cases,
        "documents": [
            {
                **document,
                "role": case["role"],
                "table_family": case["table_family"],
                "typography": case["typography"],
            }
            for case in cases
            for document in case["documents"]
        ],
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate the pinned non-Fischer natural numeric corpus."
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    corpus = json.loads(args.corpus.read_text())
    report = evaluate(args.root, corpus)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"natural numeric corpus: primary {report['primary']['agree']}/"
        f"{report['cells']} agree, {report['primary']['disagree']} disagree; "
        f"reader {report['reader']['agree']} agree, "
        f"{report['reader']['disagree']} disagree, "
        f"{report['reader']['tool_refused']} refused"
    )
    if args.check and {
        key: report[key]
        for key in (
            "documents_examined",
            "documents_with_reviewed_cells",
            "cells",
            "primary",
            "reader",
            "structure",
        )
    } != corpus["expected"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
