"""Geometry-only missing-content probe; no production confidence or repair decisions."""

from __future__ import annotations

import math
import statistics
import unicodedata
from collections import defaultdict

from pdf2md.schema import BBox
from pdf2md.scripts import Char

# Starting hypotheses, frozen before the pilot. These are not calibrated scores.
POLICY = {
    "minimum_line_characters": 12,
    "margin_fraction": 0.05,
    "box_padding_points": 1.0,
    "dark_pixel_threshold": 200,
    "minimum_ink_fraction": 0.001,
    "render_dpi": 72,
    "furniture_edge_fraction": 0.12,
    "furniture_min_pages": 3,
    "furniture_min_page_fraction": 0.25,
    "furniture_position_tolerance": 0.01,
    "furniture_gap_heights": 1.5,
}
REPRESENTED = {"emitted", "cropped", "flagged"}


def bounds(value: dict | BBox | tuple | list) -> tuple[float, float, float, float]:
    if isinstance(value, BBox):
        values = (value.x0, value.y0, value.x1, value.y1)
    elif isinstance(value, dict):
        values = tuple(value[key] for key in ("x0", "y0", "x1", "y1"))
    else:
        values = tuple(value)
    if len(values) != 4 or not all(math.isfinite(v) for v in values):
        raise ValueError("expected four finite PDF coordinates")
    x0, y0, x1, y1 = values
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def contains(box: tuple, x: float, y: float, padding: float = 0) -> bool:
    left, bottom, right, top = box
    return left - padding <= x <= right + padding and bottom - padding <= y <= top + padding


def _runs(chars: list[Char]) -> list[list[Char]]:
    """Separate geometric lines and large horizontal gaps, including column gutters."""
    lines: list[list[Char]] = []
    for char in sorted(chars, key=lambda c: (-(c[2] + c[4]) / 2, c[1])):
        center = (char[2] + char[4]) / 2
        if lines:
            line = lines[-1]
            reference = statistics.median((c[2] + c[4]) / 2 for c in line)
            height = statistics.median(c[4] - c[2] for c in line)
            if abs(center - reference) <= max(2, height * 0.4):
                line.append(char)
                continue
        lines.append([char])
    runs = []
    for line in lines:
        ordered = sorted(line, key=lambda c: c[1])
        height = statistics.median(c[4] - c[2] for c in ordered)
        run: list[Char] = []
        for char in ordered:
            if run and char[1] - run[-1][3] > max(12, 2 * height):
                runs.append(run)
                run = []
            run.append(char)
        runs.append(run)
    return runs


def _snippet(run: list[Char]) -> str:
    pieces = []
    previous = None
    height = statistics.median(c[4] - c[2] for c in run)
    for char in run:
        if previous is not None and char[1] - previous[3] > max(1, height * 0.2):
            pieces.append(" ")
        pieces.append(char[0])
        previous = char
    return "".join(pieces)[:300]


def inspect_page(
    page_box: tuple,
    chars: list[Char] | None,
    blocks: list[dict],
    ink_fraction: float,
) -> dict:
    page_box = bounds(page_box)
    if page_box[2] <= page_box[0] or page_box[3] <= page_box[1]:
        raise ValueError("page has no visible area")
    boxes = []
    unlocated = []
    for block in blocks:
        if block["coverage_status"] not in REPRESENTED:
            continue
        if block["bbox"] is None:
            unlocated.append(block["id"])
            continue
        box = bounds(block["bbox"])
        if (box[2] <= box[0] or box[3] <= box[1]
                or min(box[2], page_box[2]) <= max(box[0], page_box[0])
                or min(box[3], page_box[3]) <= max(box[1], page_box[1])):
            unlocated.append(block["id"])
        else:
            boxes.append(box)

    result = {
        "glyph_check": "unavailable" if chars is None else "measured",
        "represented_boxes": len(boxes),
        "unlocated_block_ids": unlocated,
        "ink_fraction": round(ink_fraction, 6),
        "visible_glyphs": None,
        "uncovered_glyphs": None,
        "candidates": [],
        "body_line_keys": [],
    }
    if not boxes and not unlocated and ink_fraction >= POLICY["minimum_ink_fraction"]:
        result["candidates"].append({
            "kind": "nonblank_page_without_representation",
            "bbox": list(page_box),
            "characters": None,
            "text_hint": "",
            "actionable": True,
        })
    if chars is None:
        return result

    real = [
        char for char in chars
        if char[0].strip() and char[3] > char[1] and char[4] > char[2]
        and contains(page_box, (char[1] + char[3]) / 2, (char[2] + char[4]) / 2)
    ]
    uncovered = [
        char for char in real
        if not any(contains(box, (char[1] + char[3]) / 2, (char[2] + char[4]) / 2,
                            POLICY["box_padding_points"]) for box in boxes)
    ]
    result.update(visible_glyphs=len(real), uncovered_glyphs=len(uncovered))
    # A block without geometry may already represent any of these glyphs. Preserve
    # the count but refuse localization, rather than report all of them as omissions.
    if unlocated:
        result["glyph_check"] = "unlocated_representation"
        return result
    left, bottom, right, top = page_box
    height = top - bottom
    edge_size = height * POLICY["furniture_edge_fraction"]
    result["body_line_keys"] = [
        _line_key(_snippet(run)) for run in _runs(real)
        if min(c[2] for c in run) < top - edge_size
        and max(c[4] for c in run) > bottom + edge_size
    ]
    margin_x = (right - left) * POLICY["margin_fraction"]
    margin_y = (top - bottom) * POLICY["margin_fraction"]
    for run in _runs(uncovered):
        box = (min(c[1] for c in run), min(c[2] for c in run),
               max(c[3] for c in run), max(c[4] for c in run))
        margin = (box[2] <= left + margin_x or box[0] >= right - margin_x
                  or box[3] <= bottom + margin_y or box[1] >= top - margin_y)
        substantial = len(run) >= POLICY["minimum_line_characters"]
        edge = "top" if box[1] >= top - edge_size else "bottom" if box[3] <= bottom + edge_size else None
        other = [c for c in real if c[4] < box[1] or c[2] > box[3]]
        below = [c[4] for c in other if c[4] < box[1]]
        above = [c[2] for c in other if c[2] > box[3]]
        gap = (box[1] - max(below) if below else 0) if edge == "top" else (
            min(above) - box[3] if above else 0)
        outermost = (not above if edge == "top" else not below) if edge else False
        result["candidates"].append({
            "kind": "margin_glyphs" if margin else "uncovered_glyphs",
            "bbox": list(box),
            "characters": len(run),
            "text_hint": _snippet(run),
            "actionable": substantial and not margin,
            "furniture_edge": edge if outermost and gap >= (
                box[3] - box[1]) * POLICY["furniture_gap_heights"] else None,
            "edge_distance": ((top - box[3]) if edge == "top" else box[1] - bottom) / height,
        })
    return result


def _line_key(text: str) -> str:
    # Preserve numbers and punctuation: distinct values must never become one header.
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def filter_running_furniture(pages: list[dict]) -> None:
    """Downgrade repeated isolated edge lines, retaining every candidate and reason."""
    groups = defaultdict(list)
    body_keys = {key for page in pages for key in page.get("body_line_keys", [])}
    for page in pages:
        page["probe_flagged_before_furniture"] = page["probe_flagged"]
        for candidate in page["candidates"]:
            text = candidate["text_hint"]
            edge = candidate.get("furniture_edge")
            key = _line_key(text)
            if (candidate["actionable"] and edge and key not in body_keys
                    and sum(c.isalpha() for c in text) >= POLICY["minimum_line_characters"]):
                groups[key, edge].append((page, candidate))
    for hits in groups.values():
        supporting = sorted({page["page"] for page, _ in hits})
        positions = [c["edge_distance"] for _, c in hits]
        if (len(supporting) < POLICY["furniture_min_pages"]
                or len(supporting) / len(pages) < POLICY["furniture_min_page_fraction"]
                or max(positions) - min(positions) > POLICY["furniture_position_tolerance"]):
            continue
        for _, candidate in hits:
            candidate.update(kind="probable_running_furniture", actionable=False,
                             furniture_evidence={"pages": supporting,
                                                 "reason": "repeated isolated edge line at stable height"})
    for page in pages:
        page["probe_flagged"] = any(c["actionable"] for c in page["candidates"])
