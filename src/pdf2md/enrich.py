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

import re
from pathlib import Path

import pypdfium2 as pdfium

from pdf2md.confidence import SCRAMBLED_ABOVE, assess_equation, is_clean
from pdf2md.logging import get_logger
from pdf2md.legibility import is_garbage
from pdf2md.normalize import (
    clean_preformatted,
    clean_reading,
    has_split_ligature,
    has_split_word,
    normalize_text,
    rejoin_split_word,
    religature,
    vocabulary,
)
from pdf2md.preformat import is_preformatted
from pdf2md.schema import PROSE_TYPES, Block, BlockType, FigureRef, RawTable, TableData
from pdf2md.scripts import PageChars, apply_scripts
from pdf2md.tables import GridCell, build_gfm, build_html

log = get_logger("enrich")


def religatured(text: str, vocab) -> str:
    """Repair words the text layer fractured — ligature splits ('di ff erent') and
    diacritic splits ('Lo wdin'). Dropped f-ligatures are handled upstream at the
    glyph layer (normalize.expand_ligature_glyphs). The (cached) vocabulary is built
    via the `vocab` callable only when a candidate split is present, so clean text
    pays nothing."""
    lig = has_split_ligature(text)
    if not lig and not has_split_word(text):
        return text
    words = vocab()
    if lig:
        text = religature(text, words)
    return rejoin_split_word(text, words)


def refilled(text: str, bbox, pc) -> str:
    """Replace symbol-font garbage (a broken ToUnicode CMap the engine trusted) with
    the pdfium glyph-layer reading of the same bbox, which decodes it correctly.
    Returns the original when it isn't garbage or pdfium can't do better, so a truly
    undecodable region stays flagged downstream. Shared by prose blocks and cells."""
    if bbox is None or not is_garbage(text):
        return text
    reading = clean_reading(normalize_text(pc.text_region(bbox)))
    # Keep the original when pdfium gives nothing (empty isn't "garbage", but
    # replacing text with blank would lose the cell) or no better than the garbage.
    return reading if reading and not is_garbage(reading) else text


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
        if b.type is BlockType.CODE and pc is not None and b.bbox is not None:
            # Docling labels program/console transcripts as code, but its text is the
            # same symbol-font garbage; re-read from pdfium with line breaks preserved
            # (the layout is the content) so the code fence shows the real session.
            relines = clean_preformatted(pc.text_lines(b.bbox))
            if relines and not is_garbage(relines):
                b.text = relines
        elif b.type in PROSE_TYPES and pc is not None and b.bbox is not None:
            # A console transcript Docling mislabels as prose (rather than code): its
            # banner lines mark it preformatted, so re-read line-preserved and emit as
            # a code fence instead of letting reading-order collapse flatten it.
            lines = pc.text_lines(b.bbox)
            if is_preformatted(lines):
                b.text = clean_preformatted(lines)
                b.extra["preformatted"] = True
                continue
            # When the engine's text is symbol-font garbage (a broken ToUnicode CMap
            # the engine trusted), refill it from the pdfium glyph layer, which
            # decodes the same bbox correctly.
            swapped = refilled(b.text, b.bbox, pc)
            if swapped != b.text:
                b.text = swapped
                b.extra["text_source"] = "pdfium"
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


def enrich_tables(tables: list[TableData], raw_tables: dict[str, RawTable], glyphs) -> None:
    """Finalize each table's markup engine-agnostically: rebuild the grid with
    inline sub/superscripts recovered from glyph geometry when they're present,
    otherwise just ligature-repair the engine's own rendering."""
    for t in tables:
        pc = glyphs.page_chars(t.page)
        # A "table" that is really an ASCII-art block (console listing, monospace
        # data table with rule lines) can't be gridded; keep it as line-preserved
        # text for code-fence emission rather than a mangled grid.
        if pc is not None and t.bbox is not None:
            lines = pc.text_lines(t.bbox)
            if is_preformatted(lines, pipes=True):
                t.preformatted = clean_preformatted(lines)
                continue
        raw = raw_tables.get(t.block_id)
        rebuilt = (
            _rebuilt_table(raw, pc, glyphs.vocab, t.has_spanning_cells)
            if pc is not None and raw is not None and raw.cells
            else None
        )
        if rebuilt is not None:  # scripts helped -> diverge from the engine markup
            t.gfm, t.html = rebuilt
        else:
            t.gfm = religatured(t.gfm, glyphs.vocab)
            if t.html is not None:
                t.html = religatured(t.html, glyphs.vocab)


def enrich_figures(figures: list[FigureRef], glyphs) -> None:
    for f in figures:
        if not f.caption:
            continue
        # A caption in the broken font is symbol-font garbage like any prose; refill
        # it from the pdfium glyph layer (its own bbox), then ligature-repair.
        pc = glyphs.page_chars(f.page)
        if pc is not None and f.caption_bbox is not None:
            f.caption = refilled(f.caption, f.caption_bbox, pc)
        f.caption = religatured(f.caption, glyphs.vocab)


def _rebuilt_table(raw: RawTable, pc: PageChars, vocab, spanning: bool):
    """Rebuilt (gfm, html) when recovered scripts or a font-decode refill justify
    diverging from the engine's rendering, else None. GFM can't express spans, so a
    spanning table keeps only the HTML and leaves GFM empty rather than a misleading
    flattening."""
    refill = any(c.bbox is not None and is_garbage(c.text) for c in raw.cells)
    rebuilt = build_html(_table_grid(raw, pc, vocab, escape=True), raw.num_rows, raw.num_cols)
    if not refill and "<sub>" not in rebuilt and "<sup>" not in rebuilt:
        return None
    html = rebuilt if spanning else None
    gfm = "" if spanning else build_gfm(_table_grid(raw, pc, vocab, escape=False), raw.num_rows, raw.num_cols)
    return gfm, html


# A '|' fenced by spaces or a cell edge is a column-rule glyph this PDF draws as a
# literal separator (the refill reads it in); a '|' touching a non-space (bra-ket
# `|ψ⟩`, `|x|`) is content and kept.
_RULE_PIPE = re.compile(r"(?:(?<=\s)|^)\|(?=\s|$)")


def _table_grid(raw: RawTable, pc: PageChars, vocab, *, escape: bool) -> list[GridCell]:
    out = []
    for c in raw.cells:
        cell = refilled(c.text, c.bbox, pc)
        if cell != c.text:  # refilled from pdfium: drop captured column-rule pipes
            cell = " ".join(_RULE_PIPE.sub(" ", cell).split())
        text = apply_scripts(religatured(cell, vocab),
                             pc.scored_region(c.bbox) if c.bbox is not None else [],
                             escape=escape)
        if not escape:
            text = text.replace("|", r"\|").replace("\n", " ")
        out.append(GridCell(text=text, row=c.row, col=c.col,
                            row_span=c.row_span, col_span=c.col_span, header=c.header))
    return out
