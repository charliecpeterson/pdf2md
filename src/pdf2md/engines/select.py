"""Choosing an engine, and the one question that choice turns on.

`select_engine` is the seam's front door: the rest of the pipeline receives an
`Engine` and never learns which one. `--engine auto` asks whether the document is
a scan, because that is the only property measured to separate the engines —
over ten scanned documents converted both ways on one machine, MinerU found 217
tables against Docling's 138 and carried a structural finding on 51% of them
against 92%.

That question is asked through `GlyphIndex`, not by asking pdfium whether a page
has text. A digitised scan carrying someone else's OCR has text on every page,
drawn invisibly over the image, and a text-presence test reads a 99-page scan as
0% scanned.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pypdfium2 as pdfium

from pdf2md.config import Config
from pdf2md.engines.base import Engine
from pdf2md.enrich import GlyphIndex
from pdf2md.logging import get_logger

log = get_logger("engines")


# Above this share of pages with no usable text layer the document is a scan,
# where MinerU is the measured better reader.
_SCAN_SHARE = 0.5


def scanned_share(pdf_path: Path) -> float:
    """Fraction of pages with no usable text layer.

    Through `GlyphIndex`, not by asking pdfium whether a page has text: a
    digitised scan carrying someone else's OCR has text on every page, drawn
    invisibly over the image, and a plain text-presence test reads the 1972
    compilation -- 99 scanned pages -- as 0% scanned. `GlyphIndex.page_chars`
    already returns nothing for those pages, which is the whole point of
    `scanned_overlay`."""
    with GlyphIndex(pdf_path) as glyphs:
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            pages = len(pdf)
        finally:
            pdf.close()
        if not pages:
            return 0.0
        return sum(
            glyphs.page_chars(page) is None for page in range(1, pages + 1)
        ) / pages


def auto_engine(config: Config, pdf_path: Path | None) -> str:
    """Pick the engine for `--engine auto`: MinerU for a scan, Docling otherwise.

    Measured over ten scanned documents converted both ways on one machine,
    MinerU found 217 tables against Docling's 138 and carried a structural
    finding on 51% of them against 92%. That result is about scans; on
    born-digital pages Docling is the default for good reasons, so the decision
    is made from the one property that separates the two populations, and made
    from the PDF rather than from the conversion, because it has to be made
    first. A configured executable that is not installed falls back with a
    warning rather than failing the run."""
    if pdf_path is None:
        return "docling"
    share = scanned_share(pdf_path)
    if share < _SCAN_SHARE:
        log.info("engine auto: %.0f%% of pages have a text layer, using docling",
                 100 * (1 - share))
        return "docling"
    try:
        from pdf2md.engines.mineru import MinerUEngine

        MinerUEngine(config.mineru_executable, deskew_scans=config.deskew_scans)
    except Exception as exc:  # noqa: BLE001 - an absent optional engine is not an error
        log.warning("engine auto: %.0f%% of pages are scanned and MinerU reads those "
                    "better, but it is unavailable (%s); using docling",
                    100 * share, exc)
        return "docling"
    log.info("engine auto: %.0f%% of pages have no text layer, using mineru",
             100 * share)
    return "mineru"


def select_engine(engine: Engine | None, config: Config,
                pdf_path: Path | None = None) -> Engine:
    if engine is not None:
        return engine
    if config.engine == "auto":
        config = replace(config, engine=auto_engine(config, pdf_path))
    if config.engine == "mineru":
        from pdf2md.engines.mineru import MinerUEngine

        return MinerUEngine(
            config.mineru_executable, deskew_scans=config.deskew_scans
        )
    if config.engine == "marker":
        from pdf2md.engines.marker import MarkerEngine

        return MarkerEngine(config.marker_executable)
    from pdf2md.engines.docling import DoclingEngine

    return DoclingEngine(
        formula_enrichment=config.do_formula_enrichment,
        force_ocr=config.force_ocr,
        # --ocr-page-vlm transcribes every page itself, so skip Docling's OCR entirely (roughly
        # halves the run on a scanned book); layout/figure detection still runs, and _vlm_ocr_pages
        # enumerates the scanned pages straight from the PDF rather than from Docling's blocks.
        skip_ocr=config.ocr_page_vlm,
        artifacts_path=config.local_model_dir,
        device=config.device,
    )
