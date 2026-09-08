"""Finding a table row's key words inside one panel of a scanned page.

A second reader only helps if you know which row its output belongs to, and on a
repeated-panel table that is the hard part: the same row label appears once per
panel, so matching on text alone puts a value under the wrong element.

Two routes, in order of confidence. Where the panels align, the key words are
read from the panel's own column bounds, derived from the gaps between panel
centres. Where they do not, each key is localized against the crop it came from.
Neither guesses: a panel whose bounds overlap its neighbour's is refused.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from collections import Counter
from decimal import Decimal, InvalidOperation

from PIL import Image, ImageFilter, ImageOps

from pdf2md.table_verify import (
    _numeric_read,
    _numericish_word,
    _word_lines,
    numeric_values_equal,
    typed_value,
)
from pdf2md.tables import RepeatedPanelLayout

_SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).translate(_SUBSCRIPTS)
    normalized = normalized.translate(str.maketrans({"−": "-", "–": "-", "—": "-"}))
    return re.sub(r"\s+", "", normalized.casefold())


def _row_key_words(
    primary: str, line: list[dict[str, object]]
) -> list[dict[str, object]]:
    words = sorted(line, key=lambda word: float(word["x"]))
    for end in range(1, len(words) + 1):
        observed = " ".join(str(word["text"]) for word in words[:end])
        if _key(observed) == _key(primary):
            return words[:end]
    if typed_value(primary)[2] == "numeric":
        for word in words:
            if numeric_values_equal(primary, _numeric_read(str(word["text"]))):
                return [word]
        return []
    first_numeric = next(
        (
            index for index, word in enumerate(words)
            if _numericish_word(str(word["text"]))
        ),
        len(words),
    )
    return words[:first_numeric]

def _panel_key_bounds(
    layout: RepeatedPanelLayout,
    centers: list[tuple[int, float]] | None,
    image_width: int,
) -> dict[int, tuple[float, float]] | None:
    if centers is None:
        return None
    by_column = dict(centers)
    within_panel_gaps = []
    boundary_gaps = []
    for panel_index, start in enumerate(layout.starts):
        width = layout.panel_width(panel_index)
        panel_centers = [by_column.get(column) for column in range(start, start + width)]
        if width < 2 or any(center is None for center in panel_centers):
            return None
        gaps = [
            float(right) - float(left)
            for left, right in zip(panel_centers, panel_centers[1:])
        ]
        if any(gap <= 0 for gap in gaps):
            return None
        within_panel_gaps.extend(gaps)
        if panel_index:
            previous_start = layout.starts[panel_index - 1]
            previous_end = previous_start + layout.panel_width(panel_index - 1) - 1
            boundary_gaps.append(float(panel_centers[0]) - float(by_column[previous_end]))
    if boundary_gaps:
        typical_gap = sorted(within_panel_gaps)[len(within_panel_gaps) // 2]
        if any(gap < typical_gap * 1.5 for gap in boundary_gaps):
            return None

    bounds = {}
    for start in layout.starts:
        center = float(by_column[start])
        next_center = float(by_column[start + 1])
        half_gap = (next_center - center) / 2
        bounds[start] = (max(0.0, center - half_gap), min(image_width, center + half_gap))
    ordered = sorted(bounds.values())
    if any(left[1] >= right[0] for left, right in zip(ordered, ordered[1:])):
        return None
    return bounds

def _words_in_bounds(
    line: list[dict[str, object]], bounds: tuple[float, float]
) -> list[dict[str, object]]:
    return sorted(
        [word for word in line if bounds[0] <= float(word["x"]) <= bounds[1]],
        key=lambda word: float(word["x"]),
    )

def _aligned_panel_key_words(
    rows: list[list[str]],
    source_rows: list[int],
    layout: RepeatedPanelLayout,
    tsv: str,
    bounds: dict[int, tuple[float, float]],
) -> dict[tuple[int, int], list[dict[str, object]]] | None:
    if any(
        start >= len(rows[row_index])
        or typed_value(rows[row_index][start])[2] != "numeric"
        for row_index in source_rows
        for start in layout.starts
    ):
        return None
    candidates = {}
    for start in layout.starts:
        panel_lines = []
        for line in _word_lines(tsv):
            words = _words_in_bounds(line, bounds[start])
            if not words or not any(
                character.isdigit()
                for word in words
                for character in str(word["text"])
            ):
                continue
            panel_lines.append(words)
        panel_lines.sort(
            key=lambda words: statistics.median(float(word["y"]) for word in words)
        )
        candidates[start] = panel_lines

    anchors = [
        start for start, panel_lines in candidates.items()
        if len(panel_lines) == len(source_rows)
    ]
    if not anchors:
        return None

    heights = [
        float(word["height"])
        for panel_lines in candidates.values()
        for words in panel_lines
        for word in words
    ]
    if not heights:
        return None
    tolerance = max(3.0, statistics.median(heights) * 0.75)
    anchor_lines = candidates[anchors[0]]
    aligned = {
        (row_index, anchors[0]): anchor_lines[position]
        for position, row_index in enumerate(source_rows)
    }
    for position, row_index in enumerate(source_rows):
        anchor_y = statistics.median(
            float(word["y"]) for word in anchor_lines[position]
        )
        for start in layout.starts:
            if start == anchors[0]:
                continue
            matches = [
                words for words in candidates[start]
                if abs(
                    statistics.median(float(word["y"]) for word in words) - anchor_y
                ) <= tolerance
            ]
            if len(matches) == 1:
                aligned[row_index, start] = matches[0]
    return aligned

def _localized_panel_key_words(
    rows: list[list[str]],
    source_rows: dict[int, list[int]],
    layout: RepeatedPanelLayout,
    tsv: str,
    bounds: dict[int, tuple[float, float]],
) -> tuple[
    dict[tuple[int, int], list[dict[str, object]]],
    dict[tuple[int, int], str],
]:
    aligned = {}
    refusals = {}
    lines = _word_lines(tsv)
    for start in layout.starts:
        lane = []
        previous_value = None
        for row_index in source_rows[start]:
            primary = rows[row_index][start]
            try:
                value = Decimal(_numeric_read(primary))
            except InvalidOperation:
                refusals[row_index, start] = "panel_key_not_numeric"
                continue
            if previous_value is not None and value <= previous_value:
                refusals[row_index, start] = "panel_key_not_increasing"
                continue
            previous_value = value
            lane.append((row_index, primary))

        panel_lines = []
        for line in lines:
            words = _words_in_bounds(line, bounds[start])
            if words and any(
                character.isdigit()
                for word in words
                for character in str(word["text"])
            ):
                panel_lines.append(words)
        panel_lines.sort(
            key=lambda words: statistics.median(float(word["y"]) for word in words)
        )

        proposals = {}
        for row_index, primary in lane:
            matches = [
                (line_index, key_words)
                for line_index, words in enumerate(panel_lines)
                if (key_words := _row_key_words(primary, words))
            ]
            if len(matches) == 1:
                proposals[row_index] = matches[0]
            else:
                refusals[row_index, start] = (
                    "panel_key_match_ambiguous" if matches
                    else "panel_key_match_missing"
                )

        line_uses = Counter(line_index for line_index, _ in proposals.values())
        accepted = []
        previous_y = None
        for row_index, _ in lane:
            proposal = proposals.get(row_index)
            if proposal is None:
                continue
            line_index, key_words = proposal
            if line_uses[line_index] != 1:
                refusals[row_index, start] = "panel_key_match_ambiguous"
                continue
            y = statistics.median(float(word["y"]) for word in key_words)
            if previous_y is not None and y <= previous_y:
                refusals[row_index, start] = "panel_key_vertical_order"
                continue
            previous_y = y
            accepted.append((row_index, key_words))

        required = 1 if len(lane) < 3 else (len(lane) + 1) // 2
        if len(accepted) < required:
            for row_index, _ in lane:
                refusals.setdefault(row_index, start, "panel_lane_alignment_unavailable")
            continue
        for row_index, key_words in accepted:
            aligned[row_index, start] = key_words
    return aligned, refusals

def _service_crop(
    image: Image.Image, words: list[dict[str, object]]
) -> tuple[Image.Image, list[int]]:
    left = min(int(word["left"]) for word in words)
    top = min(int(word["top"]) for word in words)
    right = max(int(word["left"]) + int(word["width"]) for word in words)
    bottom = max(int(word["top"]) + int(word["height"]) for word in words)
    height = max(bottom - top, 1)
    pad_x = max(6, round(height * 0.4))
    pad_y = max(4, round(height * 0.25))
    box = [
        max(0, left - pad_x),
        max(0, top - pad_y),
        min(image.width, right + pad_x),
        min(image.height, bottom + pad_y),
    ]
    crop = ImageOps.autocontrast(ImageOps.grayscale(image.crop(tuple(box))))
    crop = crop.resize((crop.width * 4, crop.height * 4), Image.Resampling.LANCZOS)
    crop = crop.filter(ImageFilter.UnsharpMask(radius=1, percent=100, threshold=3))
    canvas = Image.new("RGB", (max(640, crop.width), max(192, crop.height)), "white")
    canvas.paste(
        crop,
        ((canvas.width - crop.width) // 2, (canvas.height - crop.height) // 2),
    )
    crop.close()
    return canvas, box
