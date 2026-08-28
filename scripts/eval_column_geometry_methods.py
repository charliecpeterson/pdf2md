"""Compare conservative column locators without using recognized data values.

Header lanes are source-checked before evaluation. Repeated-row consensus uses only
source pixels and the structural column count; PDF glyph coordinates are a separate
born-digital reference and never enter the locator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import Counter
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image


ROOT = Path(__file__).parent.parent
DEFAULT_SOURCES = ROOT / "tests" / "column_geometry_methods_sources.json"
DEFAULT_CORPUS = ROOT / "tests" / "column_geometry_methods_corpus.json"
DEFAULT_OUTPUT = ROOT / "out" / "reviews" / "column-geometry-methods-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_artifacts(root: Path, sources: dict) -> tuple[dict[str, dict], dict[str, Path]]:
    loaded = {}
    paths = {}
    for name, artifact in sources["artifacts"].items():
        path = root / artifact["path"]
        if _sha256(path) != artifact["sha256"]:
            raise ValueError(f"column geometry artifact hash mismatch: {name}")
        paths[name] = path
        if path.suffix == ".json":
            loaded[name] = json.loads(path.read_text())
    return loaded, paths


def _merged_runs(
    dark: np.ndarray,
    row_band: tuple[int, int] | list[int],
    panel_bound: tuple[int, int] | list[int],
    expected_columns: int,
) -> list[tuple[int, int]]:
    top, bottom = (int(value) for value in row_band)
    left, right = (int(value) for value in panel_bound)
    active = dark[top:bottom, left:right].any(axis=0)
    padded = np.pad(active.astype(np.int8), (1, 1))
    edges = np.diff(padded)
    raw = [
        (int(start + left), int(stop + left))
        for start, stop in zip(
            np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
        )
    ]
    merge_gap = max(2, round((right - left) / (expected_columns * 10)))
    merged = []
    for start, stop in raw:
        if merged and start - merged[-1][1] <= merge_gap:
            merged[-1] = (merged[-1][0], stop)
        else:
            merged.append((start, stop))
    return merged


def repeated_row_lanes(
    image: Image.Image,
    row_bands: list[list[int]] | list[tuple[int, int]],
    panel_bound: list[int] | tuple[int, int],
    expected_columns: int,
) -> tuple[list[tuple[int, int]] | None, dict, str | None]:
    """Choose column separators by their support across distinct source rows."""
    if expected_columns < 2:
        raise ValueError("repeated-row consensus requires at least two columns")
    if not row_bands:
        raise ValueError("row_bands must not be empty")
    left, right = (int(value) for value in panel_bound)
    if left < 0 or left >= right or right > image.width:
        raise ValueError("panel_bound must be within the image")
    dark = np.asarray(image.convert("L")) < 200
    merge_gap = max(2, round((right - left) / (expected_columns * 10)))
    row_runs = [
        _merged_runs(dark, row_band, panel_bound, expected_columns)
        for row_band in row_bands
    ]
    row_mask = np.zeros(dark.shape[0], dtype=bool)
    for top, bottom in row_bands:
        row_mask[int(top):int(bottom)] = True
    vertical_support = dark[row_mask, left:right].mean(axis=0)
    persistent = vertical_support >= 0.95
    padded_persistent = np.pad(persistent.astype(np.int8), (1, 1))
    persistent_edges = np.diff(padded_persistent)
    rule_spans = [
        (int(start + left), int(stop + left))
        for start, stop in zip(
            np.flatnonzero(persistent_edges == 1),
            np.flatnonzero(persistent_edges == -1),
        )
    ]
    rule_separators = [round((start + stop - 1) / 2) for start, stop in rule_spans]

    # Long proportional labels can make word gaps look stronger than true column gaps.
    # Full-height rules are direct geometry and take precedence when the count is exact.
    if len(rule_separators) == expected_columns - 1:
        boundaries = [left, *rule_separators, right]
        minimum_lane_width = max(8, round((right - left) / (expected_columns * 4)))
        lanes = list(zip(boundaries, boundaries[1:]))
        if all(stop - start >= minimum_lane_width for start, stop in lanes):
            evidence = {
                "method": "persistent_vertical_rules",
                "expected_columns": expected_columns,
                "rows": len(row_bands),
                "row_run_counts": [len(runs) for runs in row_runs],
                "vertical_support_threshold": 0.95,
                "rule_spans": [list(span) for span in rule_spans],
                "selected_separators": rule_separators,
                "lanes": [list(lane) for lane in lanes],
                "minimum_lane_width": minimum_lane_width,
            }
            return lanes, evidence, None

    gaps = []
    for row_index, runs in enumerate(row_runs):
        for before, after in zip(runs, runs[1:]):
            gaps.append({
                "row": row_index,
                "center": (before[1] + after[0]) / 2,
                "width": after[0] - before[1],
            })

    tolerance = max(5, merge_gap)
    clusters: list[list[dict]] = []
    for gap in sorted(gaps, key=lambda item: item["center"]):
        if (
            clusters
            and gap["center"]
            - statistics.median(item["center"] for item in clusters[-1])
            <= tolerance
        ):
            clusters[-1].append(gap)
        else:
            clusters.append([gap])
    candidates = [
        {
            "center": statistics.median(item["center"] for item in cluster),
            "median_width": statistics.median(item["width"] for item in cluster),
            "row_support": len({item["row"] for item in cluster}),
        }
        for cluster in clusters
    ]
    minimum_support = math.ceil(len(row_bands) * 0.5)
    supported = [
        candidate
        for candidate in candidates
        if candidate["row_support"] >= minimum_support
    ]
    selected = sorted(
        supported,
        key=lambda candidate: (
            candidate["row_support"], candidate["median_width"]
        ),
        reverse=True,
    )[: expected_columns - 1]
    evidence = {
        "method": "repeated_row_separator_consensus",
        "expected_columns": expected_columns,
        "rows": len(row_bands),
        "merge_gap": merge_gap,
        "cluster_tolerance": tolerance,
        "minimum_row_support": minimum_support,
        "row_run_counts": [len(runs) for runs in row_runs],
        "candidates": sorted(candidates, key=lambda item: item["center"]),
    }
    if len(selected) != expected_columns - 1:
        return None, evidence, "stable_separators_unavailable"

    separators = sorted(round(candidate["center"]) for candidate in selected)
    boundaries = [left, *separators, right]
    minimum_lane_width = max(8, round((right - left) / (expected_columns * 4)))
    lanes = list(zip(boundaries, boundaries[1:]))
    if any(stop - start < minimum_lane_width for start, stop in lanes):
        return None, evidence, "stable_separator_lane_too_narrow"
    evidence["selected_separators"] = separators
    evidence["lanes"] = [list(lane) for lane in lanes]
    evidence["minimum_lane_width"] = minimum_lane_width
    return lanes, evidence, None


def _lane_rows(
    image: Image.Image,
    row_bands: list[list[int]] | list[tuple[int, int]],
    lanes: list[list[int]] | list[tuple[int, int]],
) -> tuple[int, int]:
    dark = np.asarray(image.convert("L")) < 200
    exact = 0
    for top, bottom in row_bands:
        if all(dark[top:bottom, left:right].any() for left, right in lanes):
            exact += 1
    return exact, len(row_bands) - exact


def _typed_rows(
    image: Image.Image,
    row_bands: list[list[int]] | list[tuple[int, int]],
    panel_bound: list[int],
    lanes: list[tuple[int, int]],
    column_types: list[str],
) -> tuple[int, int]:
    dark = np.asarray(image.convert("L")) < 200
    exact = 0
    for row_band in row_bands:
        runs = _merged_runs(dark, row_band, panel_bound, len(lanes))
        counts = [0] * len(lanes)
        for start, stop in runs:
            center = (start + stop) / 2
            lane = _lane_index(center, lanes)
            if lane is not None:
                counts[lane] += 1
        accepted = all(
            count == 1 if kind == "numeric" else count >= 1
            for count, kind in zip(counts, column_types)
        )
        exact += accepted
    return exact, len(row_bands) - exact


def _lane_index(
    x: float,
    lanes: list[list[int]] | list[tuple[int, int]],
) -> int | None:
    for index, (left, right) in enumerate(lanes):
        if left <= x < right:
            return index
    return None


def _glyph_comparison(
    pdf_path: Path,
    page_number: int,
    bbox: list[float] | None,
    image_size: tuple[int, int],
    row_bands: list[list[int]] | list[tuple[int, int]],
    reference_lanes: list[list[int]],
    candidate_lanes: list[tuple[int, int]] | None,
) -> dict:
    if bbox is None:
        return {"status": "no_text_layer_reference", "checked": 0}
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        page = document[page_number - 1]
        text = page.get_textpage()
        x0, y0, x1, y1 = bbox
        width, height = image_size
        checked = agree = disagree = 0
        for index in range(text.count_chars()):
            left, bottom, right, top = text.get_charbox(index)
            center_x = (left + right) / 2
            center_y = (bottom + top) / 2
            if not (x0 <= center_x < x1 and y0 <= center_y < y1):
                continue
            pixel_x = (center_x - x0) / (x1 - x0) * width
            pixel_y = (y1 - center_y) / (y1 - y0) * height
            if not any(top_px <= pixel_y < bottom_px for top_px, bottom_px in row_bands):
                continue
            reference = _lane_index(pixel_x, reference_lanes)
            candidate = _lane_index(pixel_x, candidate_lanes or [])
            if reference is None:
                continue
            checked += 1
            if candidate == reference:
                agree += 1
            else:
                disagree += 1
    finally:
        document.close()
    return {
        "status": "available" if checked else "no_glyphs_in_data_rows",
        "checked": checked,
        "agree": agree,
        "disagree": disagree,
    }


def _checked_result(report: dict) -> dict:
    return {
        key: report[key]
        for key in (
            "sources_sha256",
            "panels",
            "rows",
            "baseline_row_runs",
            "header_fixed_lanes",
            "repeated_row_consensus",
            "typed_consensus",
            "selected_cells",
            "pdf_glyph_reference",
            "per_panel",
        )
    }


def evaluate(root: Path, sources_path: Path) -> dict:
    sources = json.loads(sources_path.read_text())
    if sources.get("schema_version") != 1:
        raise ValueError("unsupported column geometry sources schema_version")
    artifacts, artifact_paths = _load_artifacts(root, sources)
    aligned = {
        panel["id"]: panel
        for panel in artifacts["alignment_report"]["panels_report"]
    }
    heldout_by_panel = {}
    for record in artifacts["heldout_manifest"]["records"]:
        heldout_by_panel.setdefault(record["panel_id"], []).append(record)

    totals = {
        "baseline": Counter(),
        "header": Counter(),
        "consensus": Counter(),
        "typed": Counter(),
        "cells": Counter(),
        "glyphs": Counter(),
    }
    panel_reports = []
    for panel in sources["panels"]:
        crop_path = root / panel["source_crop"]
        if _sha256(crop_path) != panel["source_crop_sha256"]:
            raise ValueError(f"column geometry crop hash mismatch: {panel['id']}")
        if panel["row_bands"] == "alignment_report":
            row_bands = aligned[panel["id"]]["projection"]["bands"]
            baseline = aligned[panel["id"]]["column_projection"]
            baseline_exact = int(baseline["exact_rows"])
            baseline_refused = int(baseline["refused_rows"])
        else:
            row_bands = panel["row_bands"]
            with Image.open(crop_path) as image:
                dark = np.asarray(image.convert("L")) < 200
                counts = [
                    len(_merged_runs(
                        dark, row_band, panel["panel_bound"], len(panel["reference_lanes"])
                    ))
                    for row_band in row_bands
                ]
            baseline_exact = sum(count == len(panel["reference_lanes"]) for count in counts)
            baseline_refused = len(counts) - baseline_exact
            baseline = {"row_run_counts": counts}

        with Image.open(crop_path) as image:
            image.load()
            consensus, evidence, consensus_refusal = repeated_row_lanes(
                image,
                row_bands,
                panel["panel_bound"],
                len(panel["reference_lanes"]),
            )
            header_exact, header_refused = _lane_rows(
                image, row_bands, panel["reference_lanes"]
            )
            if consensus is None:
                consensus_exact = typed_exact = 0
                consensus_refused_rows = typed_refused = len(row_bands)
            else:
                consensus_exact, consensus_refused_rows = _lane_rows(
                    image, row_bands, consensus
                )
                typed_exact, typed_refused = _typed_rows(
                    image,
                    row_bands,
                    panel["panel_bound"],
                    consensus,
                    panel["column_types"],
                )

            selected = heldout_by_panel.get(panel["id"], [])
            cell_agree = cell_disagree = cell_refused = 0
            for record in selected:
                column = int(record["column"]) - int(panel["column_offset"])
                run = record.get("projection_x_run")
                if consensus is None or run is None or not 0 <= column < len(consensus):
                    cell_refused += 1
                    continue
                center = sum(run) / 2
                if consensus[column][0] <= center < consensus[column][1]:
                    cell_agree += 1
                else:
                    cell_disagree += 1

            glyph = _glyph_comparison(
                artifact_paths[panel["source_artifact"]],
                int(panel["page"]),
                panel["pdf_bbox"],
                image.size,
                row_bands,
                panel["reference_lanes"],
                consensus,
            )

        for key, value in (("exact", baseline_exact), ("refused", baseline_refused)):
            totals["baseline"][key] += value
        for key, value in (("exact", header_exact), ("refused", header_refused)):
            totals["header"][key] += value
        for key, value in (
            ("exact", consensus_exact), ("refused", consensus_refused_rows)
        ):
            totals["consensus"][key] += value
        for key, value in (("exact", typed_exact), ("refused", typed_refused)):
            totals["typed"][key] += value
        totals["cells"].update({
            "agree": cell_agree,
            "disagree": cell_disagree,
            "tool_refused": cell_refused,
        })
        totals["glyphs"].update({
            "checked": glyph.get("checked", 0),
            "agree": glyph.get("agree", 0),
            "disagree": glyph.get("disagree", 0),
            "no_reference_panels": glyph["status"] != "available",
        })
        panel_reports.append({
            "id": panel["id"],
            "rows": len(row_bands),
            "columns": len(panel["reference_lanes"]),
            "baseline": {
                "exact": baseline_exact,
                "refused": baseline_refused,
                "run_counts": baseline.get("run_counts", baseline.get("row_run_counts")),
            },
            "header_fixed": {"exact": header_exact, "refused": header_refused},
            "consensus": {
                "exact": consensus_exact,
                "refused": consensus_refused_rows,
                "lanes": [list(lane) for lane in consensus] if consensus else None,
                "refusal": consensus_refusal,
                "evidence": evidence,
            },
            "typed_consensus": {"exact": typed_exact, "refused": typed_refused},
            "selected_cells": {
                "checked": len(selected),
                "agree": cell_agree,
                "disagree": cell_disagree,
                "tool_refused": cell_refused,
            },
            "pdf_glyph_reference": glyph,
        })

    rows = sum(panel["rows"] for panel in panel_reports)
    report = {
        "schema_version": 1,
        "method": "column_geometry_method_comparison",
        "contract": {
            "header_lanes": "source-checked geometry fixed before evaluation",
            "consensus": "source pixels, row bands, and structural column count only",
            "lane_types": "numeric versus text shape only; no recognized value supplied",
            "glyph_reference": "PDF character coordinates score geometry but never enter the locator",
        },
        "sources_sha256": _sha256(sources_path),
        "panels": len(panel_reports),
        "rows": rows,
        "baseline_row_runs": dict(totals["baseline"]),
        "header_fixed_lanes": dict(totals["header"]),
        "repeated_row_consensus": dict(totals["consensus"]),
        "typed_consensus": dict(totals["typed"]),
        "selected_cells": {
            "checked": sum(totals["cells"].values()),
            **dict(totals["cells"]),
        },
        "pdf_glyph_reference": dict(totals["glyphs"]),
        "per_panel": panel_reports,
    }
    return report


def check_corpus(root: Path, corpus_path: Path, report: dict) -> bool:
    corpus = json.loads(corpus_path.read_text())
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported column geometry corpus schema_version")
    for name, artifact in corpus["artifacts"].items():
        if _sha256(root / artifact["path"]) != artifact["sha256"]:
            raise ValueError(f"column geometry corpus artifact hash mismatch: {name}")
    return _checked_result(report) == corpus["expected"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    report = evaluate(args.root, args.sources)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    consensus = report["repeated_row_consensus"]
    print(
        f"column geometry: consensus {consensus['exact']}/{report['rows']} exact rows; "
        f"selected cells {report['selected_cells']['agree']}/"
        f"{report['selected_cells']['checked']} agree"
    )
    if args.check and not check_corpus(args.root, args.corpus, report):
        raise SystemExit("column geometry corpus differs from expected results")


if __name__ == "__main__":
    main()
