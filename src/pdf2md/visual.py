"""Figure enrichment stages: chart data, labels, descriptions, and SVG exports.

These operations attach derived representations to existing figure crops. Each
stage isolates failures per figure and leaves the crop as the audit record.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pypdfium2 as pdfium

from pdf2md.calibrate import AMBIGUITY_MAX, analyze_raster
from pdf2md.confidence import plot_data_accepted
from pdf2md.config import Config
from pdf2md.describe import Describer, clean_vlm_text, vision_cache_key
from pdf2md.digitize import (
    VectorPathDigitizer,
    pixel_fit,
    vector_ocr_digitize_page,
    vlm_digitize,
    vlm_digitize_consensus,
    write_estimate_composite,
)
from pdf2md.enrich import GlyphIndex
from pdf2md.labels import (
    extract_caption,
    figure_labels,
    figure_labels_ocr,
    figure_labels_textlayer,
    load_figure_ocr,
)
from pdf2md.logging import Progress, get_logger
from pdf2md.render import svg_crop
from pdf2md.schema import BBox, Block, BlockType, Digitization, FigureRef
from pdf2md.vision_cache import CacheStats, load_vision_cache, write_vision_cache

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


def _digitize_figures(
    figures,
    pdf_path: Path,
    config: Config,
    describer,
    vdir: Path,
    *,
    progress: Progress | None = None,
    cache_stats: CacheStats | None = None,
) -> dict[str, int]:
    """Attach recovered data to each figure. Tier 1 reads born-digital vector charts
    (near-lossless); tier 2 (--digitize-vlm) estimates the rest with the vision model at
    low confidence. Best-effort and per-figure isolated: one bad figure never aborts the
    rest, and a figure tier 1 already read is not re-estimated."""
    digitizer = VectorPathDigitizer()
    reader = unloaded = object()
    cache = load_vision_cache(vdir.parent, cache_stats) if config.digitize_vlm else {}

    def _reader():
        nonlocal reader
        if reader is unloaded:
            reader = load_figure_ocr()
        return reader

    by_page: dict[int, list[FigureRef]] = {}
    for fig in figures:
        if fig.bbox is not None:
            by_page.setdefault(fig.page, []).append(fig)

    pdf = None
    if by_page:
        try:
            pdf = pdfium.PdfDocument(str(pdf_path))
        except Exception as exc:  # noqa: BLE001 - VLM fallback can still use rendered crops
            log.warning("could not open source for vector digitization: %s", exc)

    completed = 0
    recovered = 0
    ocr_axis_attempts = 0
    ocr_axis_ineligible = 0
    failed = 0
    total = sum(len(page_figures) for page_figures in by_page.values())
    try:
        for page_number, page_figures in by_page.items():
            page = None
            if pdf is not None:
                try:
                    page = pdf[page_number - 1]
                except Exception as exc:  # noqa: BLE001 - isolate a bad source page
                    log.warning("could not read source page %d for digitization: %s", page_number, exc)
            for fig in page_figures:
                geometry = None
                series_geometry = None
                had_error = False
                error_note = ""
                try:
                    if page is not None:
                        fig.digitization, geometry = digitizer.digitize_page_with_geometry(
                            page, fig.bbox
                        )
                    if (
                        page is not None
                        and fig.digitization is None
                        and geometry is not None
                        and config.digitize_figures
                        and fig.asset_path
                    ):
                        series_geometry = digitizer.has_series_geometry(page, geometry)
                        if series_geometry:
                            ocr_axis_attempts += 1
                            fig.digitization = vector_ocr_digitize_page(
                                page, fig.bbox, vdir / fig.asset_path, _reader(),
                                padding_pts=config.crop_padding_pts,
                                geometry=geometry,
                            )
                        else:
                            ocr_axis_ineligible += 1
                except Exception as exc:  # noqa: BLE001 - figure-level isolate-and-skip
                    had_error = True
                    error_note = str(exc)
                    log.warning("digitize failed for %s: %s", fig.block_id, exc)
                if (
                    fig.digitization is None
                    and config.digitize_vlm
                    and describer is not None
                    and fig.asset_path
                ):
                    crop = vdir / fig.asset_path
                    try:
                        scan = analyze_raster(crop, _reader())
                        if scan is not None and scan.ambiguity > AMBIGUITY_MAX:
                            fig.digitization = Digitization(
                                [], "raster-gated", 0.0,
                                f"pixel pre-scan found ~{scan.ambiguity:.0f} overlapping ink traces "
                                "per column — too tangled for a trustworthy automated read")
                        else:
                            digitize_kwargs = dict(
                                cache=cache, endpoint=config.vlm_base_url,
                                max_tokens=config.vlm_max_tokens,
                            )
                            if config.digitize_consensus_votes > 1:
                                fig.digitization = vlm_digitize_consensus(
                                    crop, describer, scan.calibration if scan else None,
                                    votes=config.digitize_consensus_votes,
                                    temperature=config.digitize_consensus_temperature,
                                    **digitize_kwargs,
                                )
                            else:
                                fig.digitization = vlm_digitize(
                                    crop, describer, scan.calibration if scan else None,
                                    **digitize_kwargs,
                                )
                        if fig.digitization is not None and fig.digitization.series:
                            name = f"{fig.block_id.strip('#/').replace('/', '_')}_verify.png"
                            write_estimate_composite(
                                crop, fig.digitization.series, vdir / "assets" / name
                            )
                            agree = pixel_fit(crop, fig.digitization.series)
                            fig.digitization.confidence = round(
                                fig.digitization.confidence * agree, 2
                            )
                            fig.digitization.note += f"; pixel fit {agree:.2f}"
                            fig.digitization.verify_asset = f"assets/{name}"
                    except Exception as exc:  # noqa: BLE001 - figure-level isolate-and-skip
                        had_error = True
                        error_note = str(exc)
                        log.warning("vlm digitize failed for %s: %s", fig.block_id, exc)
                if fig.digitization is not None and fig.digitization.series:
                    recovered += 1
                    d = fig.digitization
                    if plot_data_accepted(d):
                        fig.data_extraction_status = "extracted"
                        fig.data_extraction_note = d.note
                    else:
                        fig.data_extraction_status = "data_withheld"
                        fig.data_extraction_note = (
                            f"candidate confidence {d.confidence:.2f} is below the emission floor"
                        )
                    log.info("digitized %s: %s, %d series, confidence %.2f",
                             fig.block_id, d.method, len(d.series), d.confidence)
                elif fig.digitization is not None:
                    fig.data_extraction_status = "digitization_refused"
                    fig.data_extraction_note = fig.digitization.note
                elif had_error:
                    failed += 1
                    fig.data_extraction_status = "digitization_failed"
                    fig.data_extraction_note = error_note or "figure reader failed"
                elif page is None:
                    failed += 1
                    fig.data_extraction_status = "digitization_failed"
                    fig.data_extraction_note = "source page was unavailable"
                elif geometry is not None:
                    # One message for every unmatched figure said only that
                    # something had failed, which is the least useful thing to
                    # record about the largest population in the corpus: 840 of
                    # 1,855 figures land here. `has_series_geometry` already
                    # separates the two causes, and the OCR-axis gate has
                    # usually just asked it, so naming which one costs nothing.
                    fig.data_extraction_status = "vector_archetype_unmatched"
                    cause = {
                        True: "the frames hold line, scatter or bar geometry, so it is the "
                              "axis calibration that failed",
                        False: "no line, scatter or bar geometry was found inside them, so "
                               "there is no series to recover",
                        None: "axis calibration or the supported line, scatter, and bar "
                              "readers did not produce accepted data",
                    }[series_geometry]
                    fig.data_extraction_note = (
                        f"{len(geometry.frames)} vector plot frame(s) detected, but {cause}"
                    )
                else:
                    try:
                        raster_source = digitizer.has_raster_image(page, fig.bbox)
                    except Exception as exc:  # noqa: BLE001 - keep figure failures isolated
                        failed += 1
                        fig.data_extraction_status = "digitization_failed"
                        fig.data_extraction_note = str(exc)
                        log.warning("figure source inspection failed for %s: %s", fig.block_id, exc)
                    else:
                        if raster_source:
                            fig.data_extraction_status = "raster_source"
                            fig.data_extraction_note = (
                                "an embedded raster image overlaps the figure; no vector "
                                "plot data is present"
                            )
                        else:
                            fig.data_extraction_status = "no_chart_geometry"
                            fig.data_extraction_note = (
                                "no supported plot frame or embedded raster chart was detected"
                            )
                completed += 1
                if progress is not None:
                    progress.count(
                        "digitizing figures",
                        completed,
                        total,
                        unit="figures",
                        detail=(
                            f"{recovered} recovered, {ocr_axis_attempts} OCR-axis attempts, "
                            f"{ocr_axis_ineligible} geometrically ineligible"
                        ),
                    )
    finally:
        if pdf is not None:
            pdf.close()
    if config.digitize_vlm:
        write_vision_cache(vdir.parent, cache)
    return {
        "attempted": completed,
        "accepted": recovered,
        "declined": completed - recovered - failed,
        "failed": failed,
        "ocr_axis_attempted": ocr_axis_attempts,
        "ocr_axis_ineligible": ocr_axis_ineligible,
    }


def _ocr_scanned_figures(figures, ocr_pages: set[int], vdir: Path) -> None:
    """Re-OCR each scanned figure's crop upright, replacing the engine's rotated read."""
    scanned = [f for f in figures if f.page in ocr_pages and f.asset_path]
    if not scanned:
        return
    reader = load_figure_ocr()
    if reader is None:
        log.warning("RapidOCR unavailable; skipping upright figure re-OCR")
        return
    for fig in scanned:
        try:
            labels = figure_labels_ocr(vdir / fig.asset_path, reader)
        except Exception as exc:  # noqa: BLE001 - figure-level isolate-and-skip
            log.warning("figure OCR failed for %s: %s", fig.block_id, exc)
            continue
        if labels is not None:
            fig.labels = labels
            log.info("upright figure OCR for %s: %d chars", fig.block_id, len(labels.text))


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


def _label_figures(figures, describer, config: Config, vdir: Path, doc_dir: Path,
                   pdf_path: Path, *, cache_stats: CacheStats | None = None) -> None:
    """Attach printed labels using the text layer or a cached vision read."""
    cache = load_vision_cache(doc_dir, cache_stats)
    with GlyphIndex(pdf_path, force_ocr=config.force_ocr) as glyphs:
        for fig in figures:
            if not fig.asset_path:
                continue
            try:
                labels = figure_labels_textlayer(glyphs.page_chars(fig.page), fig.bbox)
                source = "text-layer"
                if labels is None:
                    max_tokens = config.figure_labels_max_tokens or config.vlm_max_tokens
                    labels = figure_labels(vdir / fig.asset_path, describer,
                                           config.ocr_consensus_votes,
                                           config.ocr_consensus_temperature, cache,
                                           max_tokens, endpoint=config.vlm_base_url)
                    source = "vlm"
            except Exception as exc:  # noqa: BLE001 - figure-level isolate-and-skip
                log.warning("figure-labels failed for %s: %s", fig.block_id, exc)
                continue
            if labels is not None:
                fig.labels = labels
                log.info("figure labels for %s: %d chars (%s)",
                         fig.block_id, len(fig.labels.text), source)
    write_vision_cache(doc_dir, cache)


def _describe_crops(figures, blocks, describer: Describer, vdir: Path,
                    config: Config | None = None, *,
                    cache_stats: CacheStats | None = None) -> None:
    """Add cached vision descriptions to figures, tables, and equations."""
    cache = load_vision_cache(vdir.parent, cache_stats)
    endpoint = config.vlm_base_url if config else ""
    max_tokens = config.vlm_max_tokens if config else None

    def described(crop_rel: str, kind: str, context: str) -> str | None:
        path = vdir / crop_rel
        key = vision_cache_key(
            path, describer, kind, context=context, max_tokens=max_tokens, endpoint=endpoint
        )
        raw = cache.get(key)
        if raw is None:
            kwargs = {"max_tokens": max_tokens} if config else {}
            raw = describer.describe(path, kind, context, **kwargs)
            if raw:
                cache[key] = raw
        return (clean_vlm_text(raw)[0] or None) if raw else None

    for fig in figures:
        if fig.asset_path:
            desc = described(fig.asset_path, "figure", fig.caption or "")
            if desc:
                fig.description = desc
    for b in blocks:
        crop = b.extra.get("crop_path")
        if not crop:
            continue
        if b.type is BlockType.EQUATION:
            if not b.extra.get("transcribed"):
                latex = described(crop, "equation", b.text or "")
                if latex:
                    b.extra["transcribed"] = latex
                    b.extra["transcribed_source"] = "vision model"
        else:
            desc = described(crop, "table", b.text or "")
            if desc:
                b.extra["description"] = desc

    write_vision_cache(vdir.parent, cache)


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
