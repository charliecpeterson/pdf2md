"""Locate table panels, rows, and columns without reading OCR tokens.

The projection locator is intentionally small and conservative. It supplies an
independent geometry check for OCR row alignment, never row text or cell values.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence

import numpy as np
from PIL import Image


def projection_cell_box(
    x_run: Sequence[int],
    row_band: Sequence[int],
    image_size: tuple[int, int],
) -> list[int]:
    """Pad tight projection ink bounds for the pinned line reader."""
    if len(x_run) != 2 or len(row_band) != 2:
        raise ValueError("x_run and row_band must each contain two coordinates")
    row_height = max(1, int(row_band[1]) - int(row_band[0]))
    pad_x = max(4, round(row_height * 0.25))
    pad_y = max(2, round(row_height * 0.15))
    width, height = image_size
    return [
        max(0, int(x_run[0]) - pad_x),
        max(0, int(row_band[0]) - pad_y),
        min(width, int(x_run[1]) + pad_x),
        min(height, int(row_band[1]) + pad_y),
    ]


def projection_lane_run(
    image: Image.Image,
    row_band: Sequence[int],
    lane_bound: Sequence[int],
) -> tuple[int, int] | None:
    """Return the full ink envelope inside one pre-established column lane."""
    if len(row_band) != 2 or len(lane_bound) != 2:
        raise ValueError("row_band and lane_bound must each contain two coordinates")
    top, bottom = (int(value) for value in row_band)
    left, right = (int(value) for value in lane_bound)
    if top < 0 or top >= bottom or bottom > image.height:
        raise ValueError("row_band must be within the image")
    if left < 0 or left >= right or right > image.width:
        raise ValueError("lane_bound must be within the image")
    dark = np.asarray(image.convert("L"))[top:bottom, left:right] < 200
    active_columns = np.flatnonzero(dark.any(axis=0))
    if not len(active_columns):
        return None
    return left + int(active_columns[0]), left + int(active_columns[-1]) + 1


def projection_panel_bounds(
    image: Image.Image,
    panel_count: int,
) -> tuple[list[tuple[int, int]] | None, dict[str, object], str | None]:
    """Find repeated table panels separated by dominant vertical whitespace."""
    if panel_count < 1:
        raise ValueError("panel_count must be positive")
    if panel_count == 1:
        bounds = [(0, image.width)]
        return bounds, {
            "method": "vertical_whitespace_projection",
            "panel_count": 1,
            "panel_bounds": [[0, image.width]],
        }, None

    gray = np.asarray(image.convert("L"))
    dark_pixels = (gray < 200).sum(axis=0)
    maximum_dark_pixels = max(2, round(image.height * 0.01))
    minimum_gap_width = max(8, round(image.width * 0.03))
    minimum_panel_width = max(8, round(image.width / (panel_count * 3)))
    minimum_gap_dominance = 2
    low_ink = dark_pixels <= maximum_dark_pixels
    padded = np.pad(low_ink.astype(np.int8), (1, 1))
    edges = np.diff(padded)
    candidates = [
        (int(left), int(right))
        for left, right in zip(
            np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
        )
        if right - left >= minimum_gap_width
        and minimum_panel_width <= (left + right) // 2
        and (left + right) // 2 <= image.width - minimum_panel_width
    ]
    ranked = sorted(candidates, key=lambda gap: gap[1] - gap[0], reverse=True)
    needed = panel_count - 1
    evidence: dict[str, object] = {
        "method": "vertical_whitespace_projection",
        "panel_count": panel_count,
        "dark_threshold": 200,
        "maximum_dark_pixels": maximum_dark_pixels,
        "minimum_gap_width": minimum_gap_width,
        "minimum_panel_width": minimum_panel_width,
        "minimum_gap_dominance": minimum_gap_dominance,
        "candidate_gaps": [list(gap) for gap in ranked],
    }
    if len(ranked) < needed:
        return None, evidence, "projection_panel_gutters_unavailable"
    selected = ranked[:needed]
    if len(ranked) > needed:
        weakest_selected = min(right - left for left, right in selected)
        strongest_rejected = ranked[needed][1] - ranked[needed][0]
        if weakest_selected < strongest_rejected * minimum_gap_dominance:
            return None, evidence, "projection_panel_gutters_ambiguous"

    selected.sort()
    bounds = []
    panel_left = 0
    for gap_left, gap_right in selected:
        bounds.append((panel_left, gap_left))
        panel_left = gap_right
    bounds.append((panel_left, image.width))
    if any(right - left < minimum_panel_width for left, right in bounds):
        return None, evidence, "projection_panel_width_too_small"
    evidence["selected_gaps"] = [list(gap) for gap in selected]
    evidence["panel_bounds"] = [list(bound) for bound in bounds]
    return bounds, evidence, None


def projection_column_runs(
    image: Image.Image,
    row_bands: Sequence[tuple[int, int]],
    panel_bound: tuple[int, int],
    expected_columns: int,
) -> tuple[
    list[list[tuple[int, int]] | None] | None,
    dict[str, object],
    str | None,
]:
    """Locate cell ink runs per row without OCR tokens or word boxes."""
    if expected_columns < 1:
        raise ValueError("expected_columns must be positive")
    if not row_bands:
        raise ValueError("row_bands must not be empty")
    panel_left, panel_right = panel_bound
    if panel_left < 0 or panel_left >= panel_right or panel_right > image.width:
        raise ValueError("panel_bound must be within the image")
    if any(
        top < 0
        or top >= bottom
        or bottom > image.height
        or (index and top < row_bands[index - 1][1])
        for index, (top, bottom) in enumerate(row_bands)
    ):
        raise ValueError("row_bands must be ordered within the image")

    dark = np.asarray(image.convert("L")) < 200
    panel_width = panel_right - panel_left
    merge_gap = max(2, round(panel_width / (expected_columns * 10)))
    rows: list[list[tuple[int, int]] | None] = []
    run_counts = []
    for top, bottom in row_bands:
        active = dark[top:bottom, panel_left:panel_right].any(axis=0)
        padded = np.pad(active.astype(np.int8), (1, 1))
        edges = np.diff(padded)
        raw_runs = [
            (int(left + panel_left), int(right + panel_left))
            for left, right in zip(
                np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
            )
        ]
        merged: list[tuple[int, int]] = []
        for left, right in raw_runs:
            if merged and left - merged[-1][1] <= merge_gap:
                merged[-1] = (merged[-1][0], right)
            else:
                merged.append((left, right))
        run_counts.append(len(merged))
        rows.append(merged if len(merged) == expected_columns else None)

    exact_rows = sum(row is not None for row in rows)
    evidence: dict[str, object] = {
        "method": "row_band_horizontal_ink_runs",
        "panel_bound": [panel_left, panel_right],
        "expected_columns": expected_columns,
        "dark_threshold": 200,
        "merge_gap": merge_gap,
        "rows": len(rows),
        "exact_rows": exact_rows,
        "refused_rows": len(rows) - exact_rows,
        "run_counts": run_counts,
    }
    if not exact_rows:
        return None, evidence, "projection_columns_unavailable"
    return rows, evidence, None


def projection_row_bands(
    image: Image.Image,
    expected_rows: int,
    *,
    panel_index: int = 0,
    panel_count: int = 1,
    stripe_fraction: float = 0.25,
    panel_bounds: Sequence[tuple[int, int]] | None = None,
) -> tuple[list[tuple[int, int]] | None, dict[str, object], str | None]:
    """Return ordered horizontal ink bands for one table panel."""
    if expected_rows < 1:
        raise ValueError("expected_rows must be positive")
    if panel_count < 1 or not 0 <= panel_index < panel_count:
        raise ValueError("panel_index must identify a panel in panel_count")
    if not 0 < stripe_fraction <= 1:
        raise ValueError("stripe_fraction must be greater than zero and at most one")

    gray = np.asarray(image.convert("L"))
    if panel_bounds is None:
        panel_left = round(image.width * panel_index / panel_count)
        panel_right = round(image.width * (panel_index + 1) / panel_count)
    else:
        if len(panel_bounds) != panel_count:
            raise ValueError("panel_bounds must contain one bound per panel")
        if any(
            left < 0
            or left >= right
            or right > image.width
            or (index and left < panel_bounds[index - 1][1])
            for index, (left, right) in enumerate(panel_bounds)
        ):
            raise ValueError("panel_bounds must be ordered within the image")
        panel_left, panel_right = panel_bounds[panel_index]
    stripe_right = round(
        panel_left + (panel_right - panel_left) * stripe_fraction
    )
    stripe = gray[:, panel_left:stripe_right]
    stripe_width = stripe.shape[1]
    minimum_pixels = max(1, round(stripe_width * 0.005))
    dark_pixels = (stripe < 200).sum(axis=1)
    active = dark_pixels >= minimum_pixels
    padded = np.pad(active.astype(np.int8), (1, 1))
    edges = np.diff(padded)
    raw_bands = [
        (int(top), int(bottom), int(dark_pixels[top:bottom].max()))
        for top, bottom in zip(
            np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
        )
        if bottom - top >= 3
    ]
    nonrule_bands = [
        band for band in raw_bands if band[2] / stripe_width < 0.8
    ]
    evidence: dict[str, object] = {
        "method": "leading_panel_stripe_horizontal_ink_projection",
        "panel_index": panel_index,
        "panel_count": panel_count,
        "stripe_bounds": [panel_left, stripe_right],
        "stripe_fraction": stripe_fraction,
        "dark_threshold": 200,
        "minimum_dark_pixels": minimum_pixels,
        "raw_bands": len(raw_bands),
        "nonrule_bands": len(nonrule_bands),
        "expected_rows": expected_rows,
    }
    if panel_bounds is not None:
        evidence["panel_bounds"] = [list(bound) for bound in panel_bounds]
    if not nonrule_bands:
        evidence["text_bands"] = 0
        return None, evidence, "projection_rows_unavailable"

    median_height = statistics.median(
        bottom - top for top, bottom, _ in nonrule_bands
    )
    minimum_height = max(3, int(median_height * 0.4))
    text_bands = [
        (top, bottom)
        for top, bottom, _ in nonrule_bands
        if bottom - top >= minimum_height
    ]
    evidence.update({
        "minimum_band_height": minimum_height,
        "text_bands": len(text_bands),
    })
    if len(text_bands) < expected_rows:
        return None, evidence, "projection_row_count_mismatch"

    selected = text_bands[-expected_rows:]
    evidence["selected_bands"] = len(selected)
    evidence["bands"] = [list(band) for band in selected]
    return selected, evidence, None
