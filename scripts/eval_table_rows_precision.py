"""Score the table row audit against poppler's own reading of the same region.

    uv run python scripts/eval_table_rows_precision.py OUT_DIR [--json OUT] [--quiet]

`table structure` is the second-largest action category and its only labelled
measurement is thirteen tables. The row-level findings -- merged_rows,
dropped_row_content, dropped_column -- all rest on one claim: the printed region
holds more rows than the engine's grid. `pdftotext -layout`, cropped to the
table's own box, answers that from a different PDF stack than the pdfium ink
projection the audit uses.

  upholds      poppler's line count matches pdf2md's ink-projected row count
               (within one) and not the engine's grid, so a second reader also
               sees the rows the grid is missing
  contradicts  poppler matches the engine's grid exactly and not the projection
  neither      it matches neither, so nothing here can say which is right
  both         the two counts are close enough that poppler cannot separate them
  unusable     poppler returned too little text in the region to judge

Comparing poppler's raw line count to the engine's row count would be biased by
construction and badly: `-layout` wraps a long cell over several lines, and the
region holds a caption and a header the grid never counts as rows, so poppler
reads more lines than the grid has rows on 49% of tables carrying no finding at
all. Asking instead which of the two counts it agrees with cancels that, because
the bias applies to both groups equally -- and the unconvicted tables are kept
as the control that shows it.

"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path

import pypdfium2 as pdfium

_ROW_LEVEL = {"merged_rows", "dropped_row_content", "dropped_column"}
_MIN_LINES = 3
# Poppler and the ink projection agree exactly on 75% of convicted regions; one
# line of slack covers a caption poppler folds differently, not a different
# reading of the table.
_SLACK = 1


def region_lines(pdf_path: Path, page: int, bbox: dict, origin: tuple[float, float],
                 page_height: float) -> list[str] | None:
    """Poppler's reading of one region, in layout mode.

    `-x`/`-y` are from the page's top-left in points, while pdf2md boxes are
    absolute user space with a bottom-left origin, so the page's own box corner
    has to come back out before the flip."""
    ox, oy = origin
    top, bottom = max(bbox["y0"], bbox["y1"]), min(bbox["y0"], bbox["y1"])
    args = [
        "pdftotext", "-layout", "-f", str(page), "-l", str(page),
        "-x", str(int(bbox["x0"] - ox)), "-y", str(int((oy + page_height) - top)),
        "-W", str(int(bbox["x1"] - bbox["x0"]) + 2), "-H", str(int(top - bottom) + 2),
        str(pdf_path), "-",
    ]
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return [line for line in done.stdout.splitlines() if line.strip()]


def evaluate(version_dir: Path) -> list[dict]:
    provenance = json.loads((version_dir / "provenance.json").read_text())
    source = version_dir.parent / "source.pdf"
    if not source.is_file():
        return []

    rows: list[dict] = []
    pdf = pdfium.PdfDocument(source)
    for table in provenance["tables"]:
        audit = table.get("grid_audit") or {}
        counts = audit.get("rows")
        if not table.get("bbox") or not counts:
            continue
        kinds = {f.get("kind") for f in audit.get("findings") or []}
        page = table["page"]
        box = pdf[page - 1].get_bbox()
        lines = region_lines(source, page, table["bbox"], (box[0], box[1]),
                             abs(box[3] - box[1]))
        engine, source_rows = counts["engine"], counts["source"]
        if lines is None or len(lines) < _MIN_LINES:
            verdict = "unusable"
        else:
            near_source = abs(len(lines) - source_rows) <= _SLACK
            near_engine = abs(len(lines) - engine) <= _SLACK
            if near_source and near_engine:
                verdict = "both"
            elif near_source:
                verdict = "upholds"
            elif near_engine:
                verdict = "contradicts"
            else:
                verdict = "neither"
        rows.append({
            "document": version_dir.parent.name, "block_id": table["block_id"],
            "page": page, "flagged": bool(kinds & _ROW_LEVEL),
            "source_rows": source_rows, "engine_rows": engine,
            "poppler_lines": len(lines) if lines is not None else None,
            "verdict": verdict,
        })
    return rows


def report(rows: list[dict], quiet: bool) -> None:
    grid: Counter = Counter()
    for row in rows:
        grid[(row["flagged"], row["verdict"])] += 1
    keys = ("upholds", "contradicts", "both", "neither", "unusable")
    print(f"{'':18s}" + "".join(f"{k:>13s}" for k in keys))
    for flagged, label in ((True, "row finding"), (False, "no row finding")):
        print(f"{label:18s}" + "".join(f"{grid[(flagged, k)]:>13d}" for k in keys))

    for flagged, label in ((True, "convicted tables"), (False, "control (no finding)")):
        judged = grid[(flagged, "upholds")] + grid[(flagged, "contradicts")]
        if judged:
            print(f"\n{label}: poppler upholds the projection over the grid on "
                  f"{grid[(flagged, 'upholds')] / judged:.2f} of {judged} tables it "
                  f"could separate")
    if not quiet:
        bad = [r for r in rows if r["flagged"] and r["verdict"] == "contradicts"]
        if bad:
            print("\nconvicted tables poppler reads as the grid has them:")
            for row in bad[:12]:
                print(f"  {row['document'][:26]:28s} {row['block_id']:<14} "
                      f"p{row['page']:<5d} source={row['source_rows']:<4} "
                      f"engine={row['engine_rows']:<4} poppler={row['poppler_lines']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    rows: list[dict] = []
    for document in sorted(args.out_dir.iterdir()):
        versions = sorted(document.glob("v*/provenance.json"),
                          key=lambda p: int(p.parent.name[1:])) if document.is_dir() else []
        if versions:
            rows.extend(evaluate(versions[-1].parent))
    report(rows, args.quiet)
    if args.json:
        args.json.write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
