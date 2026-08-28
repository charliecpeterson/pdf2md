"""Evaluate one-gap source-row alignment on source-pinned non-target tables.

The corpus keeps source pixels unchanged and perturbs only Tesseract's key tokens.
This isolates row-alignment safety from the natural frequency of OCR failures.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from PIL import Image

from pdf2md.line_reader import _run_tesseract, _sha256, _table_crop
from pdf2md.row_locator import (
    projection_column_runs,
    projection_panel_bounds,
    projection_row_bands,
)


ROOT = Path(__file__).parent.parent
DEFAULT_CORPUS = ROOT / "tests" / "source_row_alignment_corpus.json"
DEFAULT_OUTPUT = ROOT / "out" / "reviews" / "source-row-alignment-heldout-v1"

spec = importlib.util.spec_from_file_location(
    "eval_source_row_recovery", ROOT / "scripts" / "eval_source_row_recovery.py"
)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


def _tsv_rows(tsv: str) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    if reader.fieldnames is None:
        raise ValueError("Tesseract TSV has no header")
    return reader.fieldnames, list(reader)


def _write_tsv(fieldnames: list[str], rows: list[dict[str, str]]) -> str:
    stream = io.StringIO()
    writer = csv.DictWriter(
        stream,
        fieldnames=fieldnames,
        delimiter="\t",
        quoting=csv.QUOTE_NONE,
        escapechar="\\",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def _word_identity(word: dict[str, object]) -> tuple[int, int, int, int]:
    return (
        int(word["left"]),
        int(word["top"]),
        int(word["width"]),
        int(word["height"]),
    )


def _row_identity(row: dict[str, str]) -> tuple[int, int, int, int]:
    return tuple(int(row[name]) for name in ("left", "top", "width", "height"))


def _replace_key_words(
    tsv: str, key_words: list[dict[str, object]], replacement: str
) -> str:
    fieldnames, rows = _tsv_rows(tsv)
    identities = {_word_identity(word) for word in key_words}
    matched = [
        row for row in rows
        if row.get("level") == "5" and _row_identity(row) in identities
    ]
    if not matched:
        raise ValueError("key words are absent from Tesseract TSV")
    matched[0]["text"] = replacement
    for row in matched[1:]:
        row["text"] = ""
    return _write_tsv(fieldnames, rows)


def _duplicate_word_line(tsv: str, key_words: list[dict[str, object]]) -> str:
    fieldnames, rows = _tsv_rows(tsv)
    identities = {_word_identity(word) for word in key_words}
    key_rows = [
        row for row in rows
        if row.get("level") == "5" and _row_identity(row) in identities
    ]
    if not key_rows:
        raise ValueError("key line is absent from Tesseract TSV")
    group = tuple(key_rows[0][name] for name in ("block_num", "par_num", "line_num"))
    source_line = [
        row for row in rows
        if row.get("level") == "5"
        and tuple(row[name] for name in ("block_num", "par_num", "line_num")) == group
    ]
    next_line = str(max(int(row.get("line_num") or 0) for row in rows) + 1000)
    copies = []
    for row in source_line:
        copied = dict(row)
        copied["line_num"] = next_line
        copied["top"] = str(int(copied["top"]) + 1)
        copies.append(copied)
    return _write_tsv(fieldnames, rows + copies)


def _wrong_key_positions(row_count: int, target: int) -> list[int]:
    needed = int(0.15 * (row_count - 1)) + 1
    eligible = [
        position for position in range(row_count)
        if position not in {target - 1, target, target + 1}
    ]
    return eligible[:needed]


def _variants(panel: dict) -> list[dict]:
    target = int(panel["target_position"])
    secondary = int(panel["secondary_position"])
    return [
        {"id": "baseline", "expected": "accept", "method": "exact_position"},
        {
            "id": "one_gap",
            "expected": "accept",
            "method": "bracketed_one_gap",
            "nonnumeric": [target],
            "inferred_position": target,
        },
        {
            "id": "edge_gap",
            "expected": "refuse",
            "nonnumeric": [0],
            "reason": "one_gap_alignment_unavailable",
        },
        {
            "id": "two_gaps",
            "expected": "refuse",
            "nonnumeric": [target, secondary],
            "reason": "source_line_count_mismatch",
        },
        {
            "id": "broken_anchor",
            "expected": "refuse",
            "nonnumeric": [target],
            "wrong_numeric": [target - 1],
            "reason": "one_gap_alignment_unavailable",
        },
        {
            "id": "ambiguous_line",
            "expected": "refuse",
            "nonnumeric": [target],
            "duplicate": target,
            "reason": "inferred_source_line_ambiguous",
        },
        {
            "id": "below_exact_ratio",
            "expected": "refuse",
            "nonnumeric": [target],
            "wrong_numeric": _wrong_key_positions(len(panel["keys"]), target),
            "reason": "one_gap_alignment_unavailable",
        },
    ]


def _mutated_tsv(
    baseline_tsv: str,
    baseline_lines: list[tuple[list[dict[str, object]], list[dict[str, object]]]],
    variant: dict,
) -> str:
    mutated = baseline_tsv
    for position in variant.get("nonnumeric", []):
        mutated = _replace_key_words(mutated, baseline_lines[position][1], "Q.Loo")
    for position in variant.get("wrong_numeric", []):
        mutated = _replace_key_words(mutated, baseline_lines[position][1], "999999")
    if variant.get("duplicate") is not None:
        mutated_lines = recovery._panel_lines(mutated, variant["key_bounds"])
        target = int(variant["duplicate"])
        before_target = sum(
            position < target for position in variant.get("nonnumeric", [])
        )
        source_position = target - before_target
        if source_position <= 0:
            raise ValueError("ambiguous-line target cannot be an edge row")
        all_lines = recovery._word_lines(mutated)
        lower_y = recovery._line_y(mutated_lines[source_position - 1][1])
        upper_y = recovery._line_y(mutated_lines[source_position][1])
        candidates = []
        for line in all_lines:
            words = recovery._words_in_bounds(line, variant["key_bounds"])
            if words and lower_y < recovery._line_y(words) < upper_y:
                candidates.append(words)
        if len(candidates) != 1:
            raise ValueError("controlled gap did not produce one intervening line")
        mutated = _duplicate_word_line(mutated, candidates[0])
    return mutated


def _mapping_matches(
    aligned: list[tuple[list[dict[str, object]], list[dict[str, object]]]],
    baseline: list[tuple[list[dict[str, object]], list[dict[str, object]]]],
) -> bool:
    return len(aligned) == len(baseline) and all(
        recovery._line_y(actual[1]) == recovery._line_y(expected[1])
        for actual, expected in zip(aligned, baseline)
    )


def _validate_panel(root: Path, panel: dict) -> None:
    keys = panel["keys"]
    row_cells = panel["row_numeric_cells"]
    if len(keys) != len(row_cells) or any(count < 1 for count in row_cells):
        raise ValueError(f"invalid row numeric-cell counts: {panel['id']}")
    numeric_keys = [recovery.Decimal(key) for key in keys]
    if any(right <= left for left, right in zip(numeric_keys, numeric_keys[1:])):
        raise ValueError(f"row keys are not strictly increasing: {panel['id']}")
    if (
        len(panel["key_bounds"]) != 2
        or panel["key_bounds"][0] >= panel["key_bounds"][1]
    ):
        raise ValueError(f"invalid key bounds: {panel['id']}")
    target = int(panel["target_position"])
    secondary = int(panel["secondary_position"])
    if target <= 0 or target >= len(keys) - 1 or secondary in {target}:
        raise ValueError(f"invalid controlled gap positions: {panel['id']}")
    source = root / panel["source"]
    if not source.is_file() or _sha256(source) != panel["source_sha256"]:
        raise ValueError(f"source reference hash mismatch: {panel['id']}")


def _evaluate_variant(
    panel: dict,
    baseline_tsv: str,
    baseline_lines: list[tuple[list[dict[str, object]], list[dict[str, object]]]],
    variant: dict,
    projection_bands: list[tuple[int, int]] | None = None,
    *,
    require_projection: bool = False,
) -> dict:
    variant = {**variant, "key_bounds": tuple(panel["key_bounds"])}
    mutated = _mutated_tsv(baseline_tsv, baseline_lines, variant)
    aligned, evidence, refusal = recovery._aligned_panel_lines(
        mutated,
        tuple(panel["key_bounds"]),
        tuple(panel["keys"]),
        projection_bands,
        require_projection=require_projection,
    )
    mapping_matches = aligned is not None and _mapping_matches(aligned, baseline_lines)
    if variant["expected"] == "accept":
        if aligned is None:
            outcome = "tool_refused"
        elif (
            not mapping_matches
            or evidence["alignment_method"] != variant["method"]
            or evidence.get("inferred_source_position") != variant.get("inferred_position")
        ):
            outcome = "disagree"
        else:
            outcome = "agree"
    else:
        outcome = (
            "agree"
            if aligned is None and refusal == variant["reason"]
            else "disagree"
        )
    return {
        "id": variant["id"],
        "expected": variant["expected"],
        "outcome": outcome,
        "expected_reason": variant.get("reason"),
        "actual_reason": refusal,
        "expected_method": variant.get("method"),
        "actual_method": evidence["alignment_method"],
        "expected_inferred_position": variant.get("inferred_position"),
        "actual_inferred_position": evidence.get("inferred_source_position"),
        "exact_key_matches": evidence["exact_key_matches"],
        "mapping_matches": mapping_matches,
    }


def evaluate(root: Path, corpus: dict, output_dir: Path, tesseract: str) -> dict:
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported source-row alignment corpus schema_version")
    executable = shutil.which(tesseract)
    if executable is None:
        raise FileNotFoundError(f"Tesseract executable not found: {tesseract}")
    panel_ids = [panel["id"] for panel in corpus["panels"]]
    if len(panel_ids) != len(set(panel_ids)):
        raise ValueError("duplicate source-row alignment panel id")
    output_dir.mkdir(parents=True, exist_ok=True)
    crop_dir = output_dir / "source-crops"
    tsv_dir = output_dir / "tesseract"
    crop_dir.mkdir(exist_ok=True)
    tsv_dir.mkdir(exist_ok=True)

    panels = []
    all_cases = []
    for index, panel in enumerate(corpus["panels"], start=1):
        print(f"[{index}/{len(corpus['panels'])}] {panel['id']}", flush=True)
        _validate_panel(root, panel)
        version_dir = root / panel["version_dir"]
        provenance_path = version_dir / "provenance.json"
        if _sha256(provenance_path) != panel["provenance_sha256"]:
            raise ValueError(f"provenance hash mismatch: {panel['id']}")
        provenance = json.loads(provenance_path.read_text())
        source_pdf = version_dir.parent / "source.pdf"
        if _sha256(source_pdf) != panel["source_sha256"]:
            raise ValueError(f"source PDF hash mismatch: {panel['id']}")
        table = next(
            (table for table in provenance["tables"] if table["block_id"] == panel["block_id"]),
            None,
        )
        if table is None:
            raise ValueError(f"source block missing: {panel['id']}")
        if int(table["page"]) != int(panel["page"]):
            raise ValueError(f"source page mismatch: {panel['id']}")
        blocks = {block["id"]: block for block in provenance["blocks"]}
        crop_path = crop_dir / f"{panel['id']}.png"
        _table_crop(source_pdf, table, blocks[panel["block_id"]], crop_path)
        if _sha256(crop_path) != panel["source_crop_sha256"]:
            raise ValueError(f"source crop hash mismatch: {panel['id']}")
        baseline_tsv = _run_tesseract(executable, crop_path)
        tsv_path = tsv_dir / f"{panel['id']}.tsv"
        tsv_path.write_text(baseline_tsv)
        baseline_lines = recovery._panel_lines(
            baseline_tsv, tuple(panel["key_bounds"])
        )
        if len(baseline_lines) != len(panel["keys"]):
            raise ValueError(
                f"baseline source-line count mismatch: {panel['id']} "
                f"({len(baseline_lines)} != {len(panel['keys'])})"
            )
        panel_index = int(panel.get("projection_panel_index", 0))
        panel_count = int(panel.get("projection_panel_count", 1))
        with Image.open(crop_path) as image:
            panel_bounds, panel_detection, panel_detection_refusal = (
                projection_panel_bounds(image, panel_count)
            )
            if panel_bounds is None:
                projection_bands = None
                projection = None
                projection_refusal = panel_detection_refusal
            else:
                projection_bands, projection, projection_refusal = projection_row_bands(
                    image,
                    len(panel["keys"]),
                    panel_index=panel_index,
                    panel_count=panel_count,
                    stripe_fraction=float(panel["projection_stripe_fraction"]),
                    panel_bounds=panel_bounds,
                )
            if projection_bands is None or panel_bounds is None:
                column_rows = None
                column_projection = None
                column_projection_refusal = projection_refusal
            else:
                column_rows, column_projection, column_projection_refusal = (
                    projection_column_runs(
                        image,
                        projection_bands,
                        panel_bounds[panel_index],
                        int(panel["projection_expected_columns"]),
                    )
                )
        projection_mismatches = (
            recovery._projection_mismatches(baseline_lines, projection_bands)
            if projection_bands is not None
            else list(range(len(baseline_lines)))
        )
        cases = [
            _evaluate_variant(
                panel,
                baseline_tsv,
                baseline_lines,
                variant,
                projection_bands,
                require_projection=True,
            )
            for variant in _variants(panel)
        ]
        for case in cases:
            case["panel_id"] = panel["id"]
        all_cases.extend(cases)
        panels.append({
            "id": panel["id"],
            "source": panel["source"],
            "source_sha256": panel["source_sha256"],
            "page": panel["page"],
            "block_id": panel["block_id"],
            "rows": len(panel["keys"]),
            "numeric_cells": sum(panel["row_numeric_cells"]),
            "panel_detection": panel_detection,
            "panel_detection_refusal": panel_detection_refusal,
            "projection": projection,
            "projection_refusal": projection_refusal,
            "projection_mismatches": projection_mismatches,
            "column_projection": column_projection,
            "column_projection_refusal": column_projection_refusal,
            "column_rows_exact": (
                sum(row is not None for row in column_rows)
                if column_rows is not None
                else 0
            ),
            "source_crop": crop_path.relative_to(output_dir).as_posix(),
            "source_crop_sha256": _sha256(crop_path),
            "tesseract_tsv": tsv_path.relative_to(output_dir).as_posix(),
            "tesseract_tsv_sha256": _sha256(tsv_path),
            "cases": cases,
        })

    counts = Counter(case["outcome"] for case in all_cases)
    positive = [case for case in all_cases if case["id"] == "one_gap"]
    negative = [case for case in all_cases if case["expected"] == "refuse"]
    held_out_cells = sum(sum(panel["row_numeric_cells"]) for panel in corpus["panels"])
    report = {
        "schema_version": 1,
        "method": "controlled_cross_document_source_row_alignment",
        "contract": {
            "reference": (
                "source-checked row sequences and numeric-cell counts pinned "
                "before perturbation"
            ),
            "perturbation": "Tesseract key tokens only; source pixels and data cells are unchanged",
            "scope": (
                "row-to-cell mapping safety, not natural OCR error frequency or "
                "value recognition"
            ),
            "outcomes": ["agree", "disagree", "tool_refused"],
        },
        "corpus_sha256": corpus.get("_corpus_sha256"),
        "tesseract": {
            "executable": executable,
            "version": subprocess.run(
                [executable, "--version"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()[0],
        },
        "documents": len({panel["source_sha256"] for panel in corpus["panels"]}),
        "panels": len(panels),
        "held_out_numeric_cells": held_out_cells,
        "projection_rows_checked": sum(panel["rows"] for panel in panels),
        "projection_row_mismatches": sum(
            len(panel["projection_mismatches"]) for panel in panels
        ),
        "projection_column_panels_agree": sum(
            panel["column_rows_exact"] == panel["rows"] for panel in panels
        ),
        "projection_column_panels_tool_refused": sum(
            panel["column_rows_exact"] != panel["rows"] for panel in panels
        ),
        "projection_column_rows_exact": sum(
            panel["column_rows_exact"] for panel in panels
        ),
        "projection_column_rows_refused": sum(
            panel["rows"] - panel["column_rows_exact"] for panel in panels
        ),
        "cases_checked": len(all_cases),
        "agree": counts["agree"],
        "disagree": counts["disagree"],
        "tool_refused": counts["tool_refused"],
        "one_gap": {
            "panels": len(positive),
            "accepted_numeric_cells": sum(
                panel["numeric_cells"]
                for panel in panels
                if next(case for case in panel["cases"] if case["id"] == "one_gap")["outcome"]
                == "agree"
            ),
            "wrong_numeric_cells": sum(
                panel["numeric_cells"]
                for panel in panels
                if next(case for case in panel["cases"] if case["id"] == "one_gap")["outcome"]
                == "disagree"
            ),
            "refused_numeric_cells": sum(
                panel["numeric_cells"]
                for panel in panels
                if next(case for case in panel["cases"] if case["id"] == "one_gap")["outcome"]
                == "tool_refused"
            ),
        },
        "negative_cases": {
            "checked": len(negative),
            "correctly_refused": sum(case["outcome"] == "agree" for case in negative),
            "false_accepts": sum(case["outcome"] == "disagree" for case in negative),
        },
        "panels_report": panels,
    }
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--tesseract", default="tesseract")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    corpus = json.loads(args.corpus.read_text())
    corpus["_corpus_sha256"] = _sha256(args.corpus)
    report = evaluate(args.root, corpus, args.output, args.tesseract)
    print(
        f"source-row alignment: {report['agree']}/{report['cases_checked']} cases agree, "
        f"{report['disagree']} disagree, {report['tool_refused']} tool-refused; "
        f"one-gap cells {report['one_gap']['accepted_numeric_cells']}/"
        f"{report['held_out_numeric_cells']} accepted"
    )
    if args.check:
        expected = corpus.get("expected")
        actual = {
            "cases_checked": report["cases_checked"],
            "agree": report["agree"],
            "disagree": report["disagree"],
            "tool_refused": report["tool_refused"],
            "accepted_numeric_cells": report["one_gap"]["accepted_numeric_cells"],
            "wrong_numeric_cells": report["one_gap"]["wrong_numeric_cells"],
            "refused_numeric_cells": report["one_gap"]["refused_numeric_cells"],
            "negative_false_accepts": report["negative_cases"]["false_accepts"],
                "projection_rows_checked": report["projection_rows_checked"],
                "projection_row_mismatches": report["projection_row_mismatches"],
                "projection_column_panels_agree": report[
                    "projection_column_panels_agree"
                ],
                "projection_column_panels_tool_refused": report[
                    "projection_column_panels_tool_refused"
                ],
                "projection_column_rows_exact": report[
                    "projection_column_rows_exact"
                ],
                "projection_column_rows_refused": report[
                    "projection_column_rows_refused"
                ],
        }
        if expected is None or actual != expected:
            print(json.dumps({"expected": expected, "actual": actual}, indent=2))
            raise SystemExit(1)


if __name__ == "__main__":
    main()
