"""Figure enrichment stages: chart data, labels, descriptions, and SVG exports.

These operations attach derived representations to existing figure crops. Each
stage isolates failures per figure and leaves the crop as the audit record.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from pdf2md.confidence import plot_data_accepted
from pdf2md.labels import (
    extract_caption,
)
from pdf2md.logging import get_logger
from pdf2md.render import svg_crop
from pdf2md.schema import BBox, Block, BlockType, FigureRef

log = get_logger("visual")

_MULTIPANEL_CAPTION = re.compile(r"\b(?:left|right|top|bottom|panels?)\b", re.I)
_CONTINUED_FIGURE_CAPTION = re.compile(
    r"\bfig(?:ure)?\.?\s*\d+.*\b(?:contd|continued)\.?\b", re.I
)
_FURNITURE_COMPACT = (
    "aippublishing",
    "checkforupdates",
    "exportcitation",
    "viewonline",
    "whypublishwithus",
)


def extraction_status(
    digitization,
    *,
    had_error: bool = False,
    error_note: str = "",
    page_missing: bool = False,
    frames: int | None = None,
    series_geometry: bool | None = None,
    raster_source: bool | None = None,
) -> tuple[str, str]:
    """What became of one figure's data, and why, from the facts the reader gathered.

    Pulled out of the digitize loop because it is the most consequential branch in the
    figure path -- it decides whether numbers reach the reader at all -- and it was the
    least reachable, buried three levels into a loop that needs a PDF, an engine and a
    vision model to enter. Two defects lived here unnoticed: the OCR-axes tier returned
    None below the emission floor, so a withheld candidate reported as a failed
    calibration, and a tick-range check then pushed six more figures into that same
    untrue statement.

    Everything it needs is passed in, `raster_source` as a tri-state (True/False, or
    None when the probe was not worth running), so the probe stays lazy in the caller
    and the decision stays testable.
    """
    if digitization is not None and digitization.series:
        if plot_data_accepted(digitization):
            return "extracted", digitization.note
        return "data_withheld", (
            f"candidate confidence {digitization.confidence:.2f} is below the emission floor"
        )
    if digitization is not None:
        return "digitization_refused", digitization.note
    if had_error:
        return "digitization_failed", error_note or "figure reader failed"
    if page_missing:
        return "digitization_failed", "source page was unavailable"
    if frames is not None:
        # One message for every unmatched figure said only that something had failed,
        # which is the least useful thing to record about the largest population in the
        # corpus: 840 of 1,855 figures land here. `has_series_geometry` already
        # separates the two causes and the OCR-axis gate has usually just asked it, so
        # naming which one costs nothing.
        cause = {
            True: "the frames hold line, scatter or bar geometry, so it is the axis "
                  "calibration that failed",
            False: "no line, scatter or bar geometry was found inside them, so there is "
                   "no series to recover",
            None: "axis calibration or the supported line, scatter, and bar readers did "
                  "not produce accepted data",
        }[series_geometry]
        return "vector_archetype_unmatched", f"{frames} vector plot frame(s) detected, but {cause}"
    if raster_source:
        return "raster_source", (
            "an embedded raster image overlaps the figure; no vector plot data is present"
        )
    return "no_chart_geometry", "no supported plot frame or embedded raster chart was detected"


def _bounds(bbox: BBox) -> tuple[float, float, float, float]:
    return (
        min(bbox.x0, bbox.x1),
        min(bbox.y0, bbox.y1),
        max(bbox.x0, bbox.x1),
        max(bbox.y0, bbox.y1),
    )


def _box_gap(first: BBox, second: BBox) -> float:
    ax0, ay0, ax1, ay1 = _bounds(first)
    bx0, by0, bx1, by1 = _bounds(second)
    return max(0.0, bx0 - ax1, ax0 - bx1, by0 - ay1, ay0 - by1)


def _overlap_ratio(first: BBox, second: BBox) -> float:
    ax0, ay0, ax1, ay1 = _bounds(first)
    bx0, by0, bx1, by1 = _bounds(second)
    x_overlap = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    y_overlap = max(0.0, min(ay1, by1) - max(ay0, by0))
    x_span = min(ax1 - ax0, bx1 - bx0)
    y_span = min(ay1 - ay0, by1 - by0)
    if x_span <= 0 or y_span <= 0:
        return 0.0
    x_ratio = x_overlap / x_span
    y_ratio = y_overlap / y_span
    return max(x_ratio, y_ratio)


def _union_bbox(first: BBox, second: BBox) -> BBox:
    ax0, ay0, ax1, ay1 = _bounds(first)
    bx0, by0, bx1, by1 = _bounds(second)
    return BBox(min(ax0, bx0), max(ay1, by1), max(ax1, bx1), min(ay0, by0))


def _nearby_text(figure: FigureRef, blocks: list[Block]) -> str:
    text = [figure.labels.text] if figure.labels is not None else []
    if figure.bbox is None:
        return "\n".join(text)
    text.extend(
        block.text
        for block in blocks
        if block.page == figure.page
        and block.bbox is not None
        and block.text
        and _box_gap(figure.bbox, block.bbox) <= 18.0
    )
    return "\n".join(text)


def _is_journal_furniture(figure: FigureRef, blocks: list[Block]) -> bool:
    if figure.caption:
        return False
    compact = re.sub(r"[^a-z0-9]+", "", _nearby_text(figure, blocks).lower())
    return any(phrase in compact for phrase in _FURNITURE_COMPACT)


def _panel_candidates(figure: FigureRef, figures: list[FigureRef]) -> list[FigureRef]:
    if (
        figure.bbox is None
        or figure.caption_bbox is None
        or not figure.caption
        or not _MULTIPANEL_CAPTION.search(figure.caption)
    ):
        return []
    caption_left, _, caption_right, caption_top = _bounds(figure.caption_bbox)
    candidates = []
    for candidate in figures:
        if (
            candidate is figure
            or candidate.page != figure.page
            or candidate.bbox is None
            or candidate.caption
        ):
            continue
        left, bottom, right, _ = _bounds(candidate.bbox)
        center_x = (left + right) / 2
        if not caption_left <= center_x <= caption_right or bottom < caption_top:
            continue
        if _overlap_ratio(figure.bbox, candidate.bbox) >= 0.5:
            candidates.append(candidate)
    return candidates


def _continued_figure_fragments(
    figure: FigureRef, figures: list[FigureRef]
) -> list[FigureRef]:
    if (
        figure.bbox is None
        or figure.caption_bbox is None
        or not figure.caption
        or not _CONTINUED_FIGURE_CAPTION.search(figure.caption)
    ):
        return []
    page_figures = [
        candidate
        for candidate in figures
        if candidate.page == figure.page and candidate.bbox is not None
    ]
    if len(page_figures) < 4:
        return []
    if sum(
        bool(candidate.caption and _CONTINUED_FIGURE_CAPTION.search(candidate.caption))
        for candidate in page_figures
    ) != 1:
        return []
    caption_left, _, caption_right, caption_top = _bounds(figure.caption_bbox)
    candidates = []
    for candidate in page_figures:
        if candidate is figure:
            continue
        left, bottom, right, _ = _bounds(candidate.bbox)
        center_x = (left + right) / 2
        if caption_left - 40 <= center_x <= caption_right + 40 and bottom >= caption_top:
            candidates.append(candidate)
    return candidates if len(candidates) >= 3 else []


def _merge_figure(anchor: FigureRef, candidate: FigureRef) -> None:
    anchor.bbox = _union_bbox(anchor.bbox, candidate.bbox)
    if candidate.labels is None:
        return
    if anchor.labels is None:
        anchor.labels = candidate.labels
    else:
        anchor.labels = replace(
            anchor.labels,
            text=f"{candidate.labels.text}\n{anchor.labels.text}",
            confidence=min(anchor.labels.confidence, candidate.labels.confidence),
        )


def _continued_figure_text_bounds(figure: FigureRef, blocks: list[Block]) -> list[BBox]:
    if figure.bbox is None:
        return []
    return [
        block.bbox
        for block in blocks
        if block.page == figure.page
        and block.bbox is not None
        and block.type is not BlockType.FIGURE
        and "originalpageisofpoorquality"
        not in re.sub(r"[^a-z]+", "", block.text.lower())
        and _box_gap(figure.bbox, block.bbox) <= 24.0
    ]


def _graphic_component_blocks(
    figure: FigureRef, figures: list[FigureRef], blocks: list[Block]
) -> list[Block]:
    if (
        figure.bbox is None
        or figure.caption
        or figure.labels is not None
        or sum(candidate.page == figure.page for candidate in figures) != 1
    ):
        return []
    left, bottom, right, top = _bounds(figure.bbox)
    candidates = []
    sides = set()
    for block in blocks:
        if (
            block.page != figure.page
            or block.bbox is None
            or block.type not in {BlockType.PARAGRAPH, BlockType.HEADING, BlockType.EQUATION}
            or len(block.text) > 120
        ):
            continue
        block_left, block_bottom, block_right, block_top = _bounds(block.bbox)
        center_y = (block_bottom + block_top) / 2
        if not bottom <= center_y <= top:
            continue
        if block_right <= left and left - block_right <= 36:
            sides.add("left")
        elif block_left >= right and block_left - right <= 36:
            sides.add("right")
        else:
            continue
        candidates.append(block)
    if (
        len(candidates) < 3
        or len(sides) != 1
        or not any(block.type is BlockType.EQUATION for block in candidates)
        or sum(block.type in {BlockType.PARAGRAPH, BlockType.HEADING} for block in candidates) < 2
    ):
        return []
    return candidates


def _panel_heading_blocks(figure: FigureRef, blocks: list[Block]) -> list[Block]:
    if figure.bbox is None or not figure.caption:
        return []
    caption = " ".join(figure.caption.lower().split())
    return [
        block
        for block in blocks
        if block.page == figure.page
        and block.type is BlockType.HEADING
        and block.bbox is not None
        and block.text
        and not block.text.lstrip()[:1].isdigit()
        and " ".join(block.text.lower().split()) in caption
        and _box_gap(figure.bbox, block.bbox) <= 24.0
    ]


def clean_figure_structure(blocks: list[Block], figures: list[FigureRef]) -> dict[str, int]:
    """Remove publisher UI and join explicit panels or continued-figure fragments."""
    furniture = {
        figure.block_id for figure in figures if _is_journal_furniture(figure, blocks)
    }
    figures[:] = [figure for figure in figures if figure.block_id not in furniture]
    blocks[:] = [block for block in blocks if block.id not in furniture]

    fragments = set()
    for figure in figures:
        candidates = _continued_figure_fragments(figure, figures)
        for candidate in candidates:
            _merge_figure(figure, candidate)
            fragments.add(candidate.block_id)
        if candidates:
            for bbox in _continued_figure_text_bounds(figure, blocks):
                figure.bbox = _union_bbox(figure.bbox, bbox)

    graphic_components = 0
    for figure in figures:
        if figure.block_id in fragments:
            continue
        components = _graphic_component_blocks(figure, figures, blocks)
        for block in components:
            figure.bbox = _union_bbox(figure.bbox, block.bbox)
        graphic_components += len(components)

    merged = set()
    absorbed_headings = set()
    for figure in figures:
        if figure.block_id in fragments:
            continue
        candidates = _panel_candidates(
            figure,
            [candidate for candidate in figures if candidate.block_id not in fragments],
        )
        for candidate in candidates:
            _merge_figure(figure, candidate)
            merged.add(candidate.block_id)
        if candidates:
            for heading in _panel_heading_blocks(figure, blocks):
                figure.bbox = _union_bbox(figure.bbox, heading.bbox)
                absorbed_headings.add(heading.id)
    removed = merged | fragments
    figures[:] = [figure for figure in figures if figure.block_id not in removed]
    blocks[:] = [
        block for block in blocks
        if block.id not in removed and block.id not in absorbed_headings
    ]
    return {
        "furniture_removed": len(furniture),
        "panels_merged": len(merged),
        "fragments_merged": len(fragments),
        "graphic_components_included": graphic_components,
        "panel_headings_absorbed": len(absorbed_headings),
    }


def associate_figure_captions(blocks: list[Block], figures: list[FigureRef]) -> int:
    """Make the figure own a caption block that describes the same source region."""
    caption_blocks = [
        block
        for block in blocks
        if block.type is BlockType.CAPTION and block.bbox is not None and block.text.strip()
    ]
    associated = 0
    for figure in figures:
        if figure.caption_bbox is None:
            continue
        target = _bounds(figure.caption_bbox)
        match = next(
            (
                block
                for block in caption_blocks
                if block.page == figure.page
                and max(
                    abs(first - second)
                    for first, second in zip(_bounds(block.bbox), target, strict=True)
                )
                <= 2.0
            ),
            None,
        )
        if match is None:
            continue
        figure.caption = match.text.strip()
        match.extra["figure_caption_of"] = figure.block_id
        caption_blocks.remove(match)
        associated += 1
    return associated






def _promote_figure_captions(figures) -> None:
    """Move a recovered caption from labels when the engine did not supply one."""
    for fig in figures:
        if fig.caption or fig.labels is None:
            continue
        caption, remaining = extract_caption(fig.labels.text)
        if caption is None:
            continue
        fig.caption = caption
        fig.labels = replace(fig.labels, text=remaining) if remaining else None






def _svg_figures(figures, ocr_pages: set[int], pdf_path: Path, assets: Path) -> None:
    """Export each genuinely vector born-digital figure as SVG when possible."""
    for fig in figures:
        if fig.bbox is None or not fig.asset_path or fig.page in ocr_pages:
            continue
        name = Path(fig.asset_path).with_suffix(".svg").name
        try:
            if svg_crop(pdf_path, fig.page, fig.bbox, assets / name):
                fig.svg_path = f"assets/{name}"
        except Exception as exc:  # noqa: BLE001 - figure-level isolate-and-skip
            log.warning("svg export failed for %s: %s", fig.block_id, exc)
