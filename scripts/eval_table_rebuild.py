"""Score glyph-geometry table rebuilds against source-checked cell labels.

    uv run python scripts/eval_table_rebuild.py [--labels FILE] [--json OUT]
                                                [--dump DOC:PAGE:BLOCK_ID]

tests/glyph_table_labels.json pins exact cell values (checked against the
source) for born-digital tables in two documents. This harness rebuilds each
labelled table's grid from glyph geometry alone (`pdf2md.table_rebuild`),
anchors every labelled cell by the engine cell's own bbox center, and reports
how often the rebuild reproduces the labelled value at that position — the
measurement that decides whether a rebuild path earns any production role.

Engine readings are printed beside mismatches as context, not treated as truth.
Missing or hash-mismatched sources fail loudly (Phase 0 rule).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pypdfium2 as pdfium

from pdf2md.scripts import PageChars
from pdf2md.table_rebuild import check_table_cells, content_norm, locate, rebuild_grid

_ROOT = Path(__file__).parent.parent
_DEFAULT_LABELS = _ROOT / "tests" / "glyph_table_labels.json"

_MINUS = str.maketrans({"−": "-", "\u2011": "-", "\u2013": "-"})
# Thousands-separator thin spaces ("-2 846.292") are typesetting, not digits.
_DIGIT_GAP = re.compile(r"(?<=\d) (?=\d)")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _norm(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).translate(_MINUS)
    text = _DIGIT_GAP.sub("", text)
    return " ".join(text.split()).strip()


def _nospace(value: str | None) -> str:
    """Everything significant stripped: whitespace drifts between the layer's
    in-number spacing conventions ("-2 846", "1. 984"), so containment compares
    digit streams, not spacing."""
    return re.sub(r"\s+", "", _norm(value))


def _load_labels(path: Path) -> dict:
    if not path.is_file():
        sys.exit(f"labels missing: {path}")
    labels = json.loads(path.read_text())
    problems = []
    for doc in labels["documents"]:
        source = _ROOT / doc["source"]
        if not source.is_file():
            problems.append(f"missing source: {doc['source']}")
        elif _sha256(source) != doc["source_sha256"]:
            problems.append(f"hash mismatch: {doc['source']}")
    if problems:
        sys.exit("corpus check failed:\n  " + "\n  ".join(problems))
    return labels


def _score_table(pc: PageChars, bbox, raw_table, cells: list[dict]) -> dict:
    """Rebuild one labelled table, score its cells positionally, and score the
    per-cell glyph verifier against the labels (flag vs engine-truth)."""
    out: dict = {
        "cells": [],
        "exact": 0,
        "contained": 0,
        "mismatch": 0,
        "no_engine_cell": 0,
        "anchor_outside": 0,
    }
    grid, evidence, refusal = rebuild_grid(pc.region_chars(bbox))
    out["refusal"] = refusal
    out["evidence"] = evidence
    anchored: dict = {}
    if grid is not None:
        out["rebuilt_rows"], out["rebuilt_lanes"] = len(grid.rows), len(grid.lane_bounds)
        out["engine_rows"], out["engine_cols"] = raw_table.num_rows, raw_table.num_cols
        for cell in raw_table.cells:
            if cell.bbox is not None:
                anchored[(cell.row, cell.col)] = cell
    else:
        out["refused_cells"] = len(cells)
    check = check_table_cells(raw_table, pc, per_cell=True, region_bbox=bbox)
    verdicts = {(r["row"], r["col"]): r for r in check.get("records", [])}
    out["glyph_check"] = {
        k: v for k, v in check.items()
        if k in ("cells", "uncovered_glyphs", "uncovered_sample")
    }
    flagged_labels = Counter()
    for label in cells:
        record = {
            "page": label["page"],
            "row": label["row"],
            "column": label["column"],
            "expected": label["expected"],
        }
        if grid is None:
            record["status"] = "refused"
            out["cells"].append(record)
            continue
        engine_cell = anchored.get((label["row"], label["column"]))
        if engine_cell is None or engine_cell.bbox is None:
            out["no_engine_cell"] += 1
            record["status"] = "no_engine_cell"
            out["cells"].append(record)
            continue
        # Verifier quality: does the glyph check flag cells whose engine value
        # disagrees with the source-checked label (and leave correct ones alone)?
        verdict = verdicts.get((label["row"], label["column"]))
        if verdict is not None:
            engine_right = _norm(label["expected"]) == _norm(verdict["engine"])
            flagged = verdict["status"] in (
                "mismatch", "glyphs_without_engine", "engine_without_glyphs",
            )
            key = ("caught" if flagged else "missed") if not engine_right else (
                "false_flag" if flagged else "correct_pass"
            )
            flagged_labels[key] += 1
            record["label_vs_engine"] = "right" if engine_right else "wrong"
        cx = (engine_cell.bbox.x0 + engine_cell.bbox.x1) / 2
        cy = (engine_cell.bbox.y0 + engine_cell.bbox.y1) / 2
        pos = locate(grid, cx, cy)
        if pos is None:
            out["anchor_outside"] += 1
            record.update({"status": "anchor_outside", "got": None})
            out["cells"].append(record)
            continue
        row, lane = pos
        got = grid.rows[row][lane]
        exact = _norm(got) == _norm(label["expected"])
        # Secondary signal: the value is in this row but lanes drifted around it
        # (merged sub-columns, split numbers). Containment, not position.
        row_text = _nospace(" ".join(grid.rows[row]))
        contained = not exact and _nospace(label["expected"]) in row_text
        status = "exact" if exact else ("contained" if contained else "mismatch")
        out[status] += 1
        record.update({"status": status, "got": got, "engine": engine_cell.text})
        out["cells"].append(record)
    out["verifier"] = dict(flagged_labels)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--labels", type=Path, default=_DEFAULT_LABELS)
    parser.add_argument("--json", type=Path, default=None, help="write the full report here")
    parser.add_argument("--dump", default=None, metavar="DOC:PAGE:BLOCK",
                        help="print one rebuilt grid instead of scoring")
    args = parser.parse_args()

    labels = _load_labels(args.labels)

    if args.dump:
        _run_dump(labels, args.dump)
        return

    from pdf2md.engines.docling import DoclingEngine

    report: dict = {"documents": []}
    totals = Counter()
    for doc in labels["documents"]:
        source = _ROOT / doc["source"]
        print(f"\n=== {doc['source']} ===")
        engine = DoclingEngine(formula_enrichment=False)
        result = engine.convert(source)
        blocks = {b.id: b for b in result.blocks if b.type.name == "TABLE"}
        pdf = pdfium.PdfDocument(str(source))
        groups: dict[tuple[int, str], list[dict]] = defaultdict(list)
        for cell in doc["cells"]:
            groups[(cell["page"], cell["block_id"])].append(cell)

        doc_report = {"source": doc["source"], "tables": []}
        for (page, block_id), cells in sorted(groups.items()):
            block = blocks.get(block_id)
            raw_table = result.raw_tables.get(block_id)
            if block is None or block.bbox is None or raw_table is None:
                print(f"  p{page} {block_id}: engine produced no table region — skipped")
                continue
            pc = PageChars(pdf[page - 1])
            scored = _score_table(pc, block.bbox, raw_table, cells)
            scored.update({"page": page, "block_id": block_id})
            doc_report["tables"].append(scored)
            for key in ("exact", "contained", "mismatch", "no_engine_cell", "anchor_outside"):
                totals[key] += scored[key]
            if scored["refusal"]:
                print(f"  p{page} {block_id}: REFUSED ({scored['refusal']})")
            else:
                dims = f"{scored['rebuilt_rows']}x{scored['rebuilt_lanes']}"
                edims = f"{scored['engine_rows']}x{scored['engine_cols']}"
                print(f"  p{page} {block_id}: grid {dims} vs engine {edims}; "
                      f"{scored['exact']}/{len(cells)} positional exact, "
                      f"+{scored['contained']} contained")
            v = scored.get("verifier", {})
            if v:
                print(f"           verifier: engine cells correct-and-passed={v.get('correct_pass', 0)}, "
                      f"false-flagged={v.get('false_flag', 0)}; "
                      f"wrong cells caught={v.get('caught', 0)}, missed={v.get('missed', 0)}; "
                      f"{scored['glyph_check']['uncovered_glyphs']} uncovered glyph(s)")
        doc_report["totals"] = {
            k: sum(t.get(k, 0) for t in doc_report["tables"])
            for k in ("exact", "contained", "mismatch")
        }
        report["documents"].append(doc_report)
        pdf.close()

    print(f"\noverall: {totals['exact']} positional exact, "
          f"{totals['contained']} contained in row, "
          f"{totals['mismatch']} mismatch, "
          f"{totals['no_engine_cell']} without an engine cell, "
          f"{totals['anchor_outside']} anchors outside the rebuilt grid")

    shown = 0
    for drep in report["documents"]:
        for table in drep["tables"]:
            for cell in table["cells"]:
                if cell["status"] == "mismatch" and shown < 12:
                    shown += 1
                    print(f"  MISMATCH {drep['source']} p{cell['page']} "
                          f"r{cell['row']}c{cell['column']}: "
                          f"expected={cell['expected']!r} got={cell['got']!r} "
                          f"engine={cell['engine']!r}")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2))
        print(f"report written: {args.json}")


def _run_dump(labels: dict, spec: str) -> None:
    try:
        source_name, page_s, block_id = spec.split(":", 2)
        page = int(page_s)
    except ValueError:
        sys.exit("--dump expects DOC:PAGE:BLOCK_ID")
    source = _ROOT / source_name
    if not source.is_file():
        sys.exit(f"missing source: {source_name}")

    from pdf2md.engines.docling import DoclingEngine

    engine = DoclingEngine(formula_enrichment=False)
    result = engine.convert(source)
    block = next((b for b in result.blocks
                  if b.id == block_id and b.page == page and b.bbox is not None), None)
    if block is None:
        sys.exit(f"no table block {block_id} on page {page}")
    pc = PageChars(pdfium.PdfDocument(str(source))[page - 1])
    grid, evidence, refusal = rebuild_grid(pc.region_chars(block.bbox))
    if grid is None:
        sys.exit(f"rebuild refused: {refusal} ({evidence})")
    widths = [max(len(row[i]) for row in grid.rows) for i in range(len(grid.lane_bounds))]
    for band, row in zip(grid.line_bands, grid.rows):
        print(f"y{band[0]:6.1f}..{band[1]:<6.1f} | "
              + " | ".join(cell.ljust(w) for cell, w in zip(row, widths)))
    print(f"lanes: {[(round(a, 1), round(b, 1)) for a, b in grid.lane_bounds]}")


if __name__ == "__main__":
    main()
