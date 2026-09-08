"""Getting content out of a figure: its data, its printed text, a description.

`visual.py` decides what *is* a figure — merging panels, dropping journal
furniture, attaching captions. These four passes run after that and try to
recover something from inside the crop, in descending order of how much they can
be trusted.

The vector tier is near-lossless and default on: a born-digital chart's series
read off the drawn paths. The OCR-axis tier reads tick text off the rendered crop
when the journal outlined its fonts. The VLM tier is an estimate and says so, and
only clears the emission floor when a model-free pre-scan calibrated the axes and
the read lands in range. The crop stays authoritative at every tier, which is why
a withheld candidate is written to a file rather than dropped: a reading a person
can check beats a number nobody can see.
"""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium

from pdf2md.calibrate import AMBIGUITY_MAX, analyze_raster
from pdf2md.config import Config
from pdf2md.describe import Describer, clean_vlm_text, vision_cache_key
from pdf2md.digitize import VectorPathDigitizer, vector_ocr_digitize_page
from pdf2md.digitize_vlm import (
    pixel_fit,
    vlm_digitize,
    vlm_digitize_consensus,
    write_estimate_composite,
)
from pdf2md.enrich import GlyphIndex
from pdf2md.labels import (
    figure_labels,
    figure_labels_ocr,
    figure_labels_textlayer,
    load_figure_ocr,
)
from pdf2md.logging import Progress, get_logger
from pdf2md.schema import BlockType, Digitization, FigureRef
from pdf2md.vision_cache import CacheStats, load_vision_cache, write_vision_cache
from pdf2md.visual import extraction_status

log = get_logger("figure_passes")

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
                raster_source = None
                if (fig.digitization is None and not had_error and page is not None
                        and geometry is None):
                    try:
                        raster_source = digitizer.has_raster_image(page, fig.bbox)
                    except Exception as exc:  # noqa: BLE001 - keep figure failures isolated
                        had_error, error_note = True, str(exc)
                        log.warning("figure source inspection failed for %s: %s",
                                    fig.block_id, exc)
                fig.data_extraction_status, fig.data_extraction_note = extraction_status(
                    fig.digitization,
                    had_error=had_error,
                    error_note=error_note,
                    page_missing=page is None,
                    frames=None if geometry is None else len(geometry.frames),
                    series_geometry=series_geometry,
                    raster_source=raster_source,
                )
                if fig.data_extraction_status in ("extracted", "data_withheld"):
                    recovered += 1
                    d = fig.digitization
                    log.info("digitized %s: %s, %d series, confidence %.2f",
                             fig.block_id, d.method, len(d.series), d.confidence)
                elif fig.data_extraction_status == "digitization_failed":
                    failed += 1
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
