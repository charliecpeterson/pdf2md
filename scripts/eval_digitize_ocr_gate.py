"""Evaluate cheap geometry gates for the outlined-axis OCR chart fallback.

    uv run python scripts/eval_digitize_ocr_gate.py BUNDLE

The completed bundle supplies labels from the previous production run. The evaluator
never runs OCR; it measures whether geometry-only rules retain every figure that OCR
previously recovered while rejecting figures that would waste an OCR call.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import time

import pypdfium2 as pdfium

from pdf2md.digitize import VectorPathDigitizer
from pdf2md.figure_geometry import _fbox, _is_rect
from pdf2md.logging import Progress
from pdf2md.schema import BBox


def _features(geometry) -> dict[str, float | int]:
    max_wide_y_ratio = 0.0
    max_x_ratio = 0.0
    wide_nonflat = 0
    inside_paths = 0
    for frame in geometry.frames:
        left, right, bottom, top = _fbox(frame)
        width = right - left
        height = top - bottom
        if width <= 0 or height <= 0:
            continue
        for polyline in geometry.polylines:
            if not polyline or _is_rect(polyline):
                continue
            cx = sum(x for x, _ in polyline) / len(polyline)
            cy = sum(y for _, y in polyline) / len(polyline)
            if not (left <= cx <= right and bottom <= cy <= top):
                continue
            inside_paths += 1
            x_ratio = (max(x for x, _ in polyline) - min(x for x, _ in polyline)) / width
            y_ratio = (max(y for _, y in polyline) - min(y for _, y in polyline)) / height
            max_x_ratio = max(max_x_ratio, x_ratio)
            if x_ratio >= 0.35:
                max_wide_y_ratio = max(max_wide_y_ratio, y_ratio)
                if y_ratio >= 0.01:
                    wide_nonflat += 1
    return {
        "frames": len(geometry.frames),
        "polylines": len(geometry.polylines),
        "inside_paths": inside_paths,
        "wide_nonflat": wide_nonflat,
        "max_x_ratio": round(max_x_ratio, 4),
        "max_wide_y_ratio": round(max_wide_y_ratio, 4),
    }


def _threshold(rows: list[dict], field: str) -> dict:
    positives = [row[field] for row in rows if row["target"]]
    floor = min(positives)
    kept = [row for row in rows if row[field] >= floor]
    return {
        "field": field,
        "positive_floor": floor,
        "kept": len(kept),
        "targets_kept": sum(row["target"] for row in kept),
    }


def _rule(rows: list[dict], name: str, predicate) -> dict:
    kept = [row for row in rows if predicate(row)]
    return {
        "rule": name,
        "kept": len(kept),
        "targets_kept": sum(row["target"] for row in kept),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate geometry-only eligibility rules for OCR-axis digitization."
    )
    parser.add_argument("bundle", type=Path, help="Completed version directory.")
    parser.add_argument("--json", type=Path, help="Write the result as JSON.")
    args = parser.parse_args()

    bundle = args.bundle.expanduser().resolve()
    provenance = json.loads((bundle / "provenance.json").read_text())
    source = bundle.parent / "source.pdf"
    digitizer = VectorPathDigitizer()
    rows = []
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    progress = Progress(logging.getLogger("pdf2md.eval_digitize_ocr_gate"))
    started = time.perf_counter()

    document = pdfium.PdfDocument(str(source))
    try:
        by_page: dict[int, list[dict]] = {}
        for figure in provenance["figures"]:
            if figure.get("bbox"):
                by_page.setdefault(figure["page"], []).append(figure)
        total = sum(len(figures) for figures in by_page.values())
        completed = 0
        for page_number, figures in by_page.items():
            page = document[page_number - 1]
            for figure in figures:
                bbox = BBox(**figure["bbox"])
                primary, geometry = digitizer.digitize_page_with_geometry(page, bbox)
                if primary is None and geometry is not None:
                    method = (figure.get("digitization") or {}).get("method")
                    series_geometry = digitizer.has_series_geometry(page, geometry)
                    rows.append(
                        {
                            "block_id": figure["block_id"],
                            "page": figure["page"],
                            "target": method == "vector-path/ocr-axes",
                            "series_geometry": series_geometry,
                            **_features(geometry),
                        }
                    )
                completed += 1
                progress.count("evaluating OCR gate", completed, total, unit="figures")
    finally:
        document.close()

    targets = [row for row in rows if row["target"]]
    summary = {
        "figures": len(provenance["figures"]),
        "seconds": round(time.perf_counter() - started, 3),
        "ocr_candidates_without_gate": len(rows),
        "labelled_ocr_recoveries": len(targets),
        "target_rows": targets,
        "single_feature_thresholds": [
            _threshold(rows, field)
            for field in (
                "inside_paths",
                "wide_nonflat",
                "max_x_ratio",
                "max_wide_y_ratio",
            )
        ],
        "candidate_rules": [
            _rule(
                rows,
                "series geometry can produce data",
                lambda row: row["series_geometry"],
            ),
            _rule(rows, "wide_nonflat >= 2", lambda row: row["wide_nonflat"] >= 2),
            _rule(
                rows,
                "x_span >= 1 and y_span >= 1",
                lambda row: row["max_x_ratio"] >= 1 and row["max_wide_y_ratio"] >= 1,
            ),
            _rule(
                rows,
                "chart geometry",
                lambda row: (
                    row["inside_paths"] >= 3
                    and row["wide_nonflat"] >= 2
                    and row["polylines"] >= 5
                    and row["max_x_ratio"] >= 1
                    and row["max_wide_y_ratio"] >= 1
                ),
            ),
        ],
    }
    encoded = json.dumps(summary, indent=2)
    print(encoded)
    if args.json:
        args.json.write_text(encoded + "\n")


if __name__ == "__main__":
    main()
