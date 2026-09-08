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
from collections import defaultdict
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from pdf2md.confidence import (
    SCRAMBLED_ABOVE,
    assess_equation,
    is_clean,
    trim_runaway_repetition,
)
from pdf2md.config import Config
from pdf2md.legibility import is_garbage
from pdf2md.logging import get_logger
from pdf2md.normalize import (
    clean_preformatted,
    clean_reading,
    has_split_ligature,
    has_split_word,
    normalize_text,
    rejoin_split_word,
    religature,
    resegment_words,
    space_after_punct,
    vocabulary,
)
from pdf2md.preformat import is_preformatted
from pdf2md.schema import (
    PROSE_TYPES,
    BBox,
    Block,
    BlockType,
    FigureRef,
    RawCell,
    RawTable,
    TableData,
)
from pdf2md.scripts import PageChars, apply_scripts
from pdf2md.table_audit import audit_table
from pdf2md.table_rebuild import (
    check_table_cells,
    engine_lane_bounds,
    glyph_grid,
    grid_markdown,
)
from pdf2md.tables import GridCell, build_gfm, build_html, gfm_rows, html_tables

log = get_logger("enrich")

# Inline mathematics an engine emitted as LaTeX, which the script overlay must
# leave alone. Deliberately narrow: a lone `$` in prose (a price) is not maths.
_INLINE_MATH = re.compile(r"\$[^$\n]+\$")
# A printed equation number, trailing its equation: (1), (2), (6a).
_EQUATION_NUMBER = re.compile(r"\(\s*(\d{1,3}[a-z]?)\s*\)\s*\Z")


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


def _render_mode(obj) -> int:
    """A text object's PDF render mode. Its own function so the overlay detector
    can be exercised without authoring a PDF."""
    return pdfium_c.FPDFTextObj_GetTextRenderMode(obj.raw)


class GlyphIndex:
    """Per-document pypdfium2 glyph access: per-page `PageChars` (cached) and the
    page-text vocabulary (lazy). Engine-independent — built straight from the PDF."""

    def __init__(self, pdf_path: Path, *, force_ocr: bool = False) -> None:
        self._pdf = pdfium.PdfDocument(str(pdf_path))
        self._cache: dict[int, PageChars | None] = {}
        self._overlay: dict[int, bool] = {}
        self._vocab: set[str] | None = None
        # Under --force-ocr the embedded text layer is distrusted, so report every page as
        # having none: the doc is treated as a scan (ocr flag set, glyph-based refill/
        # religature/script overlay skipped), and the engine's re-OCR text stands.
        self._force_ocr = force_ocr

    def page_chars(self, page_no: int | None) -> PageChars | None:
        if page_no is None or self._force_ocr:
            return None
        if self.scanned_overlay(page_no):
            # A digitised scan's OCR layer is not the page's own text: it is one
            # more reading of the pixels, and on an old scan a bad one. Reporting
            # it as no layer is what routes the page down the scanned path --
            # crop authoritative, cells candidates -- instead of letting every
            # glyph check verify the engine against the same wrong characters and
            # report agreement.
            return None
        if page_no not in self._cache:
            try:
                pc = PageChars(self._pdf[page_no - 1])
                self._cache[page_no] = None if pc.empty else pc
            except Exception as exc:  # noqa: BLE001 - geometry is best-effort
                log.warning("char geometry failed on page %d: %s", page_no, exc)
                self._cache[page_no] = None
        return self._cache[page_no]

    def scanned_overlay(self, page_no: int | None) -> bool:
        """Whether the page is a scanned image with a text layer drawn over it.

        Two conditions. The page's images cover most of it, and the text drawn over
        them is *invisible* -- render mode 3, which is what an OCR overlay must use
        so it does not obscure the scan it describes. Geometry alone is not
        enough: a full-page figure plate carries labels inside its own bounds and
        looks identical by position. Render mode separates them by construction,
        and measured across 44 documents it does so cleanly -- a 1972 scan is
        900 invisible text objects on a full-page image, a figure plate is 114
        visible ones.

        Coverage is summed over the images, not required of any one of them. The
        first version wanted a single covering image, which is how five 1970s-80s
        Elsevier scans went undetected: their pages are tiled into 22 images each,
        every one of them small, with 464 invisible text objects drawn over the
        top. Those were the worst documents in a 28-paper corpus for value damage
        and the tool never named `--engine mineru` for any of them."""
        if page_no is None:
            return False
        if page_no not in self._overlay:
            try:
                self._overlay[page_no] = self._detect_overlay(self._pdf[page_no - 1])
            except Exception as exc:  # noqa: BLE001 - geometry is best-effort
                log.warning("scan detection failed on page %d: %s", page_no, exc)
                self._overlay[page_no] = False
        return self._overlay[page_no]

    def _detect_overlay(self, page) -> bool:
        width, height = page.get_size()
        if width <= 0 or height <= 0:
            return False
        covered = 0.0
        for obj in page.get_objects():
            if obj.type != _PDFIUM_IMAGE:
                continue
            x0, y0, x1, y1 = obj.get_pos()
            covered += abs(x1 - x0) * abs(y1 - y0)
        # Summed, so tiles count; overlapping tiles can exceed the page, which only
        # makes a covered page more obviously covered.
        if covered / (width * height) <= _SCAN_IMAGE_COVER:
            return False

        drawn = invisible = 0
        for obj in page.get_objects():
            if obj.type != _PDFIUM_TEXT:
                continue
            drawn += 1
            if _render_mode(obj) == _RENDER_INVISIBLE:
                invisible += 1
        # Too little text to judge: a full-page figure with a caption under it is
        # not a scan, and neither is a blank plate.
        if drawn < _SCAN_MIN_TEXT_OBJECTS:
            return False
        return invisible / drawn > _SCAN_INVISIBLE_SHARE

    @property
    def page_count(self) -> int:
        return len(self._pdf)

    def vocab(self) -> set[str]:
        # A word kept whole on any page confirms a join of its split elsewhere.
        if self._vocab is None:
            words: set[str] = set()
            for i in range(len(self._pdf)):
                try:
                    words |= vocabulary(self._pdf[i].get_textpage().get_text_bounded())
                except Exception as exc:  # noqa: BLE001 - best-effort
                    log.warning("page text read failed on page %d: %s", i + 1, exc)
            self._vocab = words
        return self._vocab

    def close(self) -> None:
        self._pdf.close()

    def __enter__(self) -> GlyphIndex:
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
            # LaTeX spells its own scripts, so the geometric overlay has nothing to
            # add and everything to break: it rewrote `$Q(1)^n$` as
            # `$Q(1)^<sup>n</sup>$`, which is neither valid LaTeX nor valid markup.
            # Only engines that emit inline mathematics reach this (Docling does not).
            if not _INLINE_MATH.search(b.text):
                b.text = apply_scripts(b.text, pc.scored_region(b.bbox))
        elif b.type is BlockType.EQUATION and b.bbox is not None:
            # Before the cross-check, or it scores the padding rather than the formula.
            b.text, runaway = trim_runaway_repetition(b.text)
            if runaway:
                b.extra["runaway_trimmed"] = runaway
            if pc is not None:
                tl = pc.text_region(b.bbox)
                # The printed number sits inside the equation's own region, so the
                # formula model consumes it and emits LaTeX without it -- 96 of the
                # numbered equations across two corpora lost theirs, which breaks
                # every "substituting into (2)" in the prose. The layer reading of
                # the same region still has it.
                #
                # Only where there is a layer to read. A scanned document has none
                # by design, so its equations keep their number the other way: they
                # are image-backed, and the crop shows what the page printed.
                number = _EQUATION_NUMBER.search(tl.strip())
                if number and number.group(1) not in b.text:
                    b.extra["equation_number"] = number.group(1)
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


def resegment_ocr_prose(blocks: list[Block], word_split: bool = True) -> None:
    """Clean up OCR'd scanned prose. The comma/semicolon spacing RapidOCR drops ('ramp,toward'
    -> 'ramp, toward') is fixed always — a missing space after a comma is wrong in any language.
    Run-together words ('Lookunderthecab' -> 'Look under the cab') are re-split only when
    `word_split` is on, since that read uses English frequencies. Born-digital text has a real
    layer and is left alone; the `ocr` flag (set in enrich_blocks) is the gate."""
    for b in blocks:
        if b.extra.get("ocr") and b.type in PROSE_TYPES and b.text.strip():
            b.text = space_after_punct(b.text)
            if word_split:
                b.text = resegment_words(b.text)


def enrich_tables(tables: list[TableData], raw_tables: dict[str, RawTable], glyphs) -> None:
    """Finalize each table's markup engine-agnostically: rebuild the grid with
    inline sub/superscripts recovered from glyph geometry when they're present,
    otherwise just ligature-repair the engine's own rendering. Born-digital
    tables additionally get per-cell glyph verification recorded as evidence."""
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
        if pc is not None and raw is not None and raw.cells:
            t.cell_glyph_check = check_table_cells(raw, pc, region_bbox=t.bbox)
        # Row-level audit and the glyph-truth reconstruction, after the markup is
        # final: both read the cells a reader will actually see.
        rows = _rendered_rows(t)
        t.grid_audit = audit_table(
            rows[0] if rows else [], rows[1:], raw, pc, t.bbox
        )
        if pc is not None and raw is not None and raw.cells:
            grid, _refusal = glyph_grid(raw, pc, t.bbox)
            if grid is not None:
                t.glyph_grid = grid_markdown(grid)
        if pc is not None and t.grid_audit.get("corroborated"):
            t.printed_lines = _printed_region(pc, t.bbox)


def _printed_region(pc: PageChars, bbox: BBox) -> str:
    """The region's printed lines, verbatim.

    Only worth keeping where the audit has already established the engine's
    arrangement is wrong. On the Lanthanides SI -- basis sets a journal typeset
    as fixed-width listings and Docling labelled tables -- the emitted grid
    holds 98.9% of the region's value tokens and the glyph-column reading 93.9%,
    while this holds all of them in the order they were printed. A grid that
    keeps every exponent and loses which coefficient it belongs to is not a
    usable basis set."""
    lines = [line.rstrip() for line in pc.text_region(bbox).splitlines()]
    return "\n".join(lines).strip("\n")


def _rendered_rows(t: TableData) -> list[list[str]]:
    """The table's cells as the reader gets them, whichever markup it ships in."""
    if (t.gfm or "").strip():
        return gfm_rows(t.gfm)
    tables = html_tables(t.html) if t.html else []
    return tables[0] if tables else []


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


def _cell_read_boxes(raw: RawTable) -> dict[int, BBox]:
    """Per cell (by index), the box to read its glyphs from: its own, widened to
    its column's lane but never past a row-neighbour.

    An engine draws a cell box a point or two inside the ink, and a glyph counts
    as inside only when its *center* is. The last character of a tight cell falls
    out, and the font-decode refill then writes the truncated reading over the
    cell: measured on the GRASP2018 contents pages, `12.1` refilled as `12.`,
    `A.1` as `A.` and `6.10` as `6.1`.

    Two bounds, because each alone fails. The column lane (`engine_lane_bounds`,
    the union of that column's single-column cells) is what an individual tight
    box lacks -- on those pages column 0 spans 90.0-122.9 while the cell holding
    `12.1` claims only 99.1-117.6. But widening to the *neighbour's* box instead
    pulls a table of contents' leader dots into the page-number cell (`13` reads
    as `. . . . . . 13` on 848 cells corpus-wide), because the gap between two
    boxes is full of them; the number column's own lane is narrow and excludes
    them. And the lane alone can overlap the next column's, so a row-neighbour
    still caps it and no glyph is read into two cells.

    Neither bound is a tolerance: both are measured off the engine's own cell
    geometry, and a cell whose column has no lane keeps its own box.
    """
    lanes = engine_lane_bounds(raw)
    columns = sorted({c.col for c in raw.cells
                      if c.bbox is not None and c.col_span == 1})
    lane_of = dict(zip(columns, lanes))

    boxes: dict[int, BBox] = {}
    rows: dict[int, list[tuple[int, RawCell]]] = defaultdict(list)
    for i, c in enumerate(raw.cells):
        if c.bbox is not None:
            rows[c.row].append((i, c))
    for row in rows.values():
        row.sort(key=lambda pair: min(pair[1].bbox.x0, pair[1].bbox.x1))
        # Left to right, each cell bounded on the left by where the previous
        # one's *read* box ended: widening then never reaches ink another cell
        # will also read, so no glyph is claimed twice. (Engine boxes that
        # already overlap stay as they are -- that is the engine's own doing.)
        prev_hi = None
        for slot, (i, c) in enumerate(row):
            own_lo = min(c.bbox.x0, c.bbox.x1)
            own_hi = max(c.bbox.x0, c.bbox.x1)
            lane = lane_of.get(c.col) if c.col_span == 1 else None
            if lane is None:
                prev_hi = own_hi
                continue
            allowed_lo, allowed_hi = lane
            if prev_hi is not None:
                allowed_lo = max(allowed_lo, prev_hi)
            if slot + 1 < len(row):
                nxt = row[slot + 1][1].bbox
                allowed_hi = min(allowed_hi, min(nxt.x0, nxt.x1))
            read_lo, read_hi = min(own_lo, allowed_lo), max(own_hi, allowed_hi)
            boxes[i] = BBox(x0=read_lo, y0=c.bbox.y0, x1=read_hi, y1=c.bbox.y1)
            prev_hi = read_hi
    return boxes


def _table_grid(raw: RawTable, pc: PageChars, vocab, *, escape: bool) -> list[GridCell]:
    out = []
    read_boxes = _cell_read_boxes(raw)
    for i, c in enumerate(raw.cells):
        cell = refilled(c.text, read_boxes.get(i, c.bbox), pc)
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


# ---------------------------------------------------------------------------
# Token-level consistency (read-only measurement, surfaced via profile.json):
# did every word and number in the embedded text layer reach the output? The
# per-block word recall rides on enrichment because the glyph geometry is
# already loaded here; the document-level numeric check runs once after emit.
# Both are informational signals — nothing downstream rewrites a block.


# Below this fraction of its source-region words surviving, a prose block is
# counted as low-recall (content likely dropped or garbled, not just reordered).
# A page is a scanned image with an OCR overlay when one image covers this
# much of it and this share of its characters sit inside that image.
_PDFIUM_TEXT = 1
_PDFIUM_IMAGE = 3
# PDF text render mode 3: draw nothing. What an OCR overlay uses so the scan
# underneath stays visible, and what page text never uses.
_RENDER_INVISIBLE = 3
_SCAN_IMAGE_COVER = 0.7
_SCAN_INVISIBLE_SHARE = 0.9
_SCAN_MIN_TEXT_OBJECTS = 20































def warn_about_scan_overlays(pdf_path: Path, ocr_pages: set[int], config: Config) -> None:
    """Say so when a document is a scan carrying someone else's OCR.

    Detecting it gets the posture right — crops authoritative, cells candidates —
    but the transcription is still whoever digitised the paper, and on an old
    scan that is the worst reading available. Measured over all 99 pages of a
    1972 data-table paper: the embedded layer leaves 22.9% of value tokens
    malformed and recovers 21% of each page's printed row grid, where MinerU
    leaves 0.6% and recovers 99%. A re-OCR through --force-ocr sits between them
    (8% on a three-page sample). Naming the better path is the point of the
    warning."""
    if not ocr_pages or config.force_ocr:
        return
    with GlyphIndex(pdf_path) as glyphs:
        overlaid = sum(glyphs.scanned_overlay(page) for page in sorted(ocr_pages))
    if overlaid:
        log.warning(
            "%d page(s) are scans carrying an embedded OCR text layer; that text is "
            "kept as a candidate beside the authoritative crops and is only as good "
            "as whoever digitised the paper. For a fresh transcription re-run with "
            "--engine mineru, which on a measured 1972 scan cut malformed value "
            "tokens from 22.9%% to 0.6%%; --force-ocr is the fallback where MinerU "
            "is unavailable.",
            overlaid,
        )
