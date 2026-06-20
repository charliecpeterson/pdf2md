"""Engine-agnostic block enrichment: the verification/fallback layer.

The engine produces blocks in reading order; this stage adds everything that
makes the output trustworthy and is independent of which engine produced it —
ligature repair and inline sub/superscripts on prose, the equation cross-check
against the embedded text layer, and scanned-page (no text layer) detection that
forces equations to be image-backed. It reads glyph geometry from pypdfium2 via a
`GlyphIndex`, so it works on any engine's `EngineResult` and is unit-testable with
a fake glyph source. A future multi-pass (re-transcribing flagged crops) is a step
added here, not in an engine adapter.
"""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium

from pdf2md.confidence import SCRAMBLED_ABOVE, assess_equation, is_clean
from pdf2md.logging import get_logger
from pdf2md.normalize import has_split_ligature, normalize_text, religature, vocabulary
from pdf2md.schema import Block, BlockType
from pdf2md.scripts import PageChars, apply_scripts

log = get_logger("enrich")

# Block types whose prose carries inline scripts and ligatures worth recovering.
_SCRIPT_TYPES = {
    BlockType.PARAGRAPH, BlockType.HEADING, BlockType.LIST,
    BlockType.CAPTION, BlockType.FOOTNOTE, BlockType.OTHER,
}


def religatured(text: str, vocab) -> str:
    """Rejoin split ligatures in any text path, building the page vocabulary
    lazily (via the `vocab` callable) only when a split is actually present."""
    return religature(text, vocab()) if has_split_ligature(text) else text


class GlyphIndex:
    """Per-document pypdfium2 glyph access: per-page `PageChars` (cached) and the
    page-text vocabulary (lazy). Engine-independent — built straight from the PDF."""

    def __init__(self, pdf_path: Path) -> None:
        self._pdf = pdfium.PdfDocument(str(pdf_path))
        self._cache: dict[int, PageChars | None] = {}
        self._vocab: set[str] | None = None

    def page_chars(self, page_no: int | None) -> PageChars | None:
        if page_no is None:
            return None
        if page_no not in self._cache:
            try:
                pc = PageChars(self._pdf[page_no - 1])
                self._cache[page_no] = None if pc.empty else pc
            except Exception as exc:  # noqa: BLE001 - geometry is best-effort
                log.warning("char geometry failed on page %d: %s", page_no, exc)
                self._cache[page_no] = None
        return self._cache[page_no]

    def vocab(self) -> set[str]:
        # A word kept whole on any page confirms a join of its split elsewhere.
        if self._vocab is None:
            words: set[str] = set()
            for i in range(len(self._pdf)):
                try:
                    words |= vocabulary(self._pdf[i].get_textpage().get_text_range())
                except Exception as exc:  # noqa: BLE001 - best-effort
                    log.warning("page text read failed on page %d: %s", i + 1, exc)
            self._vocab = words
        return self._vocab

    def close(self) -> None:
        self._pdf.close()

    def __enter__(self) -> "GlyphIndex":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def enrich_blocks(blocks: list[Block], glyphs) -> None:
    """Mutate blocks in place: ligature/script repair on prose, the equation
    text-layer cross-check, and scanned-page handling. `glyphs` is anything with
    `page_chars(page)` and `vocab()` (a `GlyphIndex`, or a fake in tests)."""
    for b in blocks:
        pc = glyphs.page_chars(b.page)
        # A page with no embedded text layer was OCR'd from a scan: the text,
        # LaTeX, and cells the engine produced are all OCR guesses, so nothing can
        # be cross-checked and the scan pixels are the only ground truth.
        if pc is None:
            b.extra["ocr"] = True
        if b.type in _SCRIPT_TYPES and pc is not None and b.bbox is not None:
            # Rejoin split ligatures (validated against the page vocabulary), then
            # overlay scripts; both align to the same glyphs.
            b.text = religatured(b.text, glyphs.vocab)
            b.text = apply_scripts(b.text, pc.scored_region(b.bbox))
        elif b.type is BlockType.EQUATION and b.bbox is not None:
            if pc is not None:
                tl = pc.text_region(b.bbox)
                assessed = assess_equation(b.text, tl)
                if assessed is not None:
                    b.confidence, reading = assessed
                    if reading is not None:
                        # Suspect extraction: the pipeline crops the equation image
                        # as the faithful source. The flat text-layer reading rides
                        # along as a hint only when clean and in reading order.
                        b.extra["text_layer"] = normalize_text(reading)
                        b.extra["ordered"] = (
                            is_clean(tl) and pc.reading_disorder(b.bbox) < SCRAMBLED_ABOVE
                        )
            else:  # no text layer to verify the OCR LaTeX -> image-back it
                b.confidence = 0.0
