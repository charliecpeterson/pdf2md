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
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pypdfium2 as pdfium

from pdf2md.confidence import SCRAMBLED_ABOVE, assess_equation, is_clean
from pdf2md.conservation import numeric_accounting, numeric_conservation
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
    resegment_words,
    space_after_punct,
    vocabulary,
)
from pdf2md.preformat import is_preformatted
from pdf2md.schema import (
    PROSE_TYPES,
    Block,
    BlockType,
    CoverageFlag,
    FigureRef,
    RawTable,
    TableData,
)
from pdf2md.scripts import PageChars, apply_scripts
from pdf2md.table_audit import audit_table
from pdf2md.table_rebuild import check_table_cells, glyph_grid, grid_markdown
from pdf2md.tables import GridCell, build_gfm, build_html, gfm_rows, html_tables

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

    def __init__(self, pdf_path: Path, *, force_ocr: bool = False) -> None:
        self._pdf = pdfium.PdfDocument(str(pdf_path))
        self._cache: dict[int, PageChars | None] = {}
        self._vocab: set[str] | None = None
        # Under --force-ocr the embedded text layer is distrusted, so report every page as
        # having none: the doc is treated as a scan (ocr flag set, glyph-based refill/
        # religature/script overlay skipped), and the engine's re-OCR text stands.
        self._force_ocr = force_ocr

    def page_chars(self, page_no: int | None) -> PageChars | None:
        if page_no is None or self._force_ocr:
            return None
        if page_no not in self._cache:
            try:
                pc = PageChars(self._pdf[page_no - 1])
                self._cache[page_no] = None if pc.empty else pc
            except Exception as exc:  # noqa: BLE001 - geometry is best-effort
                log.warning("char geometry failed on page %d: %s", page_no, exc)
                self._cache[page_no] = None
        return self._cache[page_no]

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
            record_block_recall(b, pc)
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


# ---------------------------------------------------------------------------
# Token-level consistency (read-only measurement, surfaced via profile.json):
# did every word and number in the embedded text layer reach the output? The
# per-block word recall rides on enrichment because the glyph geometry is
# already loaded here; the document-level numeric check runs once after emit.
# Both are informational signals — nothing downstream rewrites a block.

# Below this fraction of its source-region words surviving, a prose block is
# counted as low-recall (content likely dropped or garbled, not just reordered).
LOW_RECALL_BELOW = 0.90
# How much of the smaller box two prose regions may share before neither
# block's recall is decidable.
_AMBIGUOUS_REGION_SHARE = 0.15

_SCRIPT_TAGS = re.compile(r"</?(?:sub|sup)>")
_WORDS = re.compile(r"\w+")
# A word the page breaks across a line is one word; the emitter rejoins it. The
# break character is not always a hyphen: a font with no ToUnicode entry for its
# soft hyphen surfaces as U+00AD, U+FFFE, or the TeX control byte, and a metric
# that only knew about `-` would score every such line break as a lost word.
_INTRAWORD_HYPHEN = re.compile(
    "([^\\W\\d_])[-\u2010\u2011\u00ad\ufffe\x02]\\s*([^\\W\\d_])"
)


def _recall_words(text: str) -> list[str]:
    """Tokenize a reading for the recall comparison. Both sides get the same
    treatment, so the only differences left are content differences: a word the
    page breaks across lines is one word (the emitter rejoins it, and a metric
    that didn't would score every hyphenated line-break as a loss), and script
    tags are emission syntax."""
    # A space, not nothing: the source side is read script-split, so `X<sub>UFF</sub>`
    # has to tokenize as `X UFF` the way the glyph reading of the same ink does.
    # Dropping the tags instead makes every scripted run a phantom miss in one
    # direction or the other, depending on whether the engine glued or separated.
    text = unicodedata.normalize("NFKC", _SCRIPT_TAGS.sub(" ", text)).lower()
    while (joined := _INTRAWORD_HYPHEN.sub(r"\1\2", text)) != text:
        text = joined
    return _WORDS.findall(text)


def record_block_recall(block: Block, pc) -> None:
    """Measure how much of the block's source region survived into its emitted
    text: a word-multiset comparison against the glyph layer, order-insensitive
    so scrambled draw order doesn't read as loss. Stored in `extra` for
    provenance; aggregated by `recall_summary`. Skipped when the region holds no
    words (a bbox/layer mismatch measures nothing).

    The source side is read script-split, like the page prints it rather than
    like the draw order glues it: `technetium67` is two tokens on the page, and
    comparing the glued reading against correctly separated output scores the
    marker as a phantom loss.

    `strict` is the same comparison without diacritic folding, and readers of it
    default to `matched` so a bundle written before it existed reloads cleanly
    through `StoredEngine`. The gap between
    the two is accent damage specifically — an emitted `Co te` for `Côté` — which
    is a real defect but a different one from a missing word, and worth telling
    apart before either is flagged."""
    src = _recall_words(pc.region_scriptsplit(block.bbox))
    if not src:
        return
    out = _recall_words(block.text)
    src = _rejoin_split(src, Counter(out))
    strict = sum((Counter(src) & Counter(out)).values())
    folded = sum(
        (Counter(_fold(w) for w in src) & Counter(_fold(w) for w in out)).values()
    )
    block.extra["glyph_word_recall"] = {
        "matched": folded, "total": len(src), "strict": strict,
    }


def recall_review_flags(blocks: list[Block]) -> tuple[list[CoverageFlag], list[CoverageFlag]]:
    """Turn per-block recall into review actions: (marked, informational).

    The measurement has run since the first version of this file; until now it
    reached `profile.json` as a count and nothing else, so a block that lost
    words looked identical to one that didn't in the Markdown a reader opens.

    Words missing from the emitted text are an action, and the marker rides
    beside the block. Lost diacritics are separated out because they are a
    different defect with a different remedy: the content is present and
    mis-spelled (`Co te` for `Côté`), which a reader checking a reference list
    needs to know about but which does not make the block's content suspect.
    Those stay informational, so a bibliography in French or German doesn't bury
    the Markdown in markers."""
    marked: list[CoverageFlag] = []
    informational: list[CoverageFlag] = []
    ambiguous = _overlapping_regions(blocks)
    for b in blocks:
        rec = b.extra.get("glyph_word_recall")
        if not rec or not rec["total"]:
            continue
        missing = rec["total"] - rec["matched"]
        if b.id in ambiguous:
            # The recall was measured over a region another block also claims, so
            # a word "missing" here may simply belong to the neighbour. Recorded
            # in provenance, not raised: `quality.py` names region-boundary
            # accuracy as something block accounting does not measure, and this
            # is the honest form of that admission.
            informational.append(CoverageFlag(
                b.id, b.page,
                f"region boundary: this block's box overlaps a neighbour's by "
                f"{ambiguous[b.id]:.0%}, so its text-layer recall is not decidable",
                "", disposition="informational", severity="low", content_impact="low",
            ))
            continue
        if rec["matched"] / rec["total"] < LOW_RECALL_BELOW:
            reason = (
                f"text layer recall: {missing} of {rec['total']} source word(s) "
                f"reach no part of the emitted text"
            )
            severity = "high" if missing >= 5 else "medium"
            marked.append(CoverageFlag(
                b.id, b.page, reason,
                f"> **[pdf2md: action required ({severity}): {reason}; verify against "
                f"[source page {b.page}](../source.pdf#page={b.page})]**",
                disposition="action_required", severity=severity,
                content_impact=severity,
            ))
        elif rec.get("strict", rec["matched"]) < rec["matched"]:
            lost = rec["matched"] - rec["strict"]
            reason = (
                f"diacritics lost: {lost} word(s) are present but stripped of "
                f"their accents by the font decode"
            )
            informational.append(CoverageFlag(
                b.id, b.page, reason,
                f"> **[pdf2md: {reason}]**",
                disposition="informational", severity="low", content_impact="low",
            ))
    return marked, informational


def _overlapping_regions(blocks: list[Block]) -> dict[str, float]:
    """Blocks whose region another block materially claims too.

    Recall compares a block's text against the glyphs in its box, which assumes
    the box is the block's alone. Where two overlap, a word counted missing may
    belong to the neighbour -- the last surviving false positive in this metric
    was a stray numeral inside a paragraph's box. Across 951 prose blocks the
    median overlap is zero and the 97th percentile 0.085, so a sixth of the
    smaller box is far outside normal and rare enough to refuse on."""
    by_page: dict[int, list[Block]] = defaultdict(list)
    for block in blocks:
        if block.type in PROSE_TYPES and block.bbox is not None:
            by_page[block.page].append(block)

    def area(box) -> float:
        return abs(box.x1 - box.x0) * abs(box.y1 - box.y0)

    def overlap(a, b) -> float:
        wide = max(0.0, min(max(a.x0, a.x1), max(b.x0, b.x1))
                   - max(min(a.x0, a.x1), min(b.x0, b.x1)))
        high = max(0.0, min(max(a.y0, a.y1), max(b.y0, b.y1))
                   - max(min(a.y0, a.y1), min(b.y0, b.y1)))
        smaller = min(area(a), area(b))
        return (wide * high) / smaller if smaller > 0 else 0.0

    ambiguous: dict[str, float] = {}
    for page in by_page.values():
        for index, block in enumerate(page):
            worst = max(
                (overlap(block.bbox, other.bbox)
                 for other in page[:index] + page[index + 1:]),
                default=0.0,
            )
            if worst > _AMBIGUOUS_REGION_SHARE:
                ambiguous[block.id] = worst
    return ambiguous


def _rejoin_split(source: list[str], emitted: Counter) -> list[str]:
    """Merge adjacent source words whose concatenation is a word the output has.

    A styled capital or a two-run glyph draw splits one printed word across two
    source tokens with no hyphen to join on: `ReAct` reads as `reac` + `t`, and
    scoring that as two lost words is the metric measuring the draw order rather
    than the content. Only a join the output actually contains is made, which is
    a stricter validator than `normalize.rejoin_split_word`'s page vocabulary and
    is available here because the emitted text is the thing being compared."""
    merged: list[str] = []
    index = 0
    while index < len(source):
        pair = (
            source[index] + source[index + 1] if index + 1 < len(source) else None
        )
        if pair and emitted[pair]:
            merged.append(pair)
            index += 2
        else:
            merged.append(source[index])
            index += 1
    return merged


def _fold(word: str) -> str:
    """The word with its combining marks removed, so a lost accent doesn't read
    as a lost word."""
    return "".join(
        c for c in unicodedata.normalize("NFD", word)
        if not unicodedata.combining(c)
    )


def recall_summary(blocks: list[Block]) -> dict[str, int]:
    """Aggregate the per-block recalls into profile-level counts. Accent damage
    is counted apart from missing words: both are defects, but only one is a
    question about whether the content is there."""
    measured = matched = total = low = accented = 0
    for b in blocks:
        rec = b.extra.get("glyph_word_recall")
        if not rec:
            continue
        measured += 1
        matched += rec["matched"]
        total += rec["total"]
        if rec["matched"] / rec["total"] < LOW_RECALL_BELOW:
            low += 1
        elif rec.get("strict", rec["matched"]) < rec["matched"]:
            accented += 1
    return {
        "blocks_measured": measured,
        "words_total": total,
        "words_matched": matched,
        "low_recall_blocks": low,
        "accent_damaged_blocks": accented,
    }
