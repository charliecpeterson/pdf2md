"""The enrichment layer is engine-agnostic, so it's testable with a fake glyph
source — no Docling, no real PDF. These pin the orchestration (which block gets
what) that used to live untested inside the Docling adapter."""

from __future__ import annotations

import pytest

from pdf2md.enrich import (
    _cell_read_boxes,
    enrich_blocks,
    enrich_figures,
    enrich_tables,
    recall_review_flags,
    record_recall,
)
from pdf2md.schema import BBox, Block, BlockType, FigureRef, RawCell, RawTable, TableData

_BB = BBox(x0=0, y0=10, x1=10, y1=0)


class _FakePC:
    empty = False

    def __init__(self, text: str = "", scored=None, disorder: float = 0.0, lines: str = "") -> None:
        self._text, self._scored, self._disorder, self._lines = text, scored or [], disorder, lines

    def region_chars(self, bbox) -> list:
        # These fixtures carry no glyph geometry; cell verification sees none.
        return []

    def text_region(self, bbox) -> str:
        return self._text

    def region_scriptsplit(self, bbox) -> str:
        # The fixtures supply an already-separated reading; the real one splits
        # scripted groups off their base word.
        return self._text

    def text_lines(self, bbox) -> str:
        return self._lines

    def scored_region(self, bbox):
        return self._scored

    def reading_disorder(self, bbox) -> float:
        return self._disorder


class _FakeGlyphs:
    def __init__(self, pages: dict, vocab: set | None = None) -> None:
        self._pages, self._vocab = pages, vocab or set()

    def page_chars(self, page):
        return self._pages.get(page)

    def vocab(self):
        return self._vocab


def _eq(text, page=1):
    return Block(id="#/eq", type=BlockType.EQUATION, text=text, page=page, bbox=_BB)


def test_scanned_page_image_backs_equations():
    # No text layer on the page (page_chars -> None) means OCR scan: flag it and
    # force confidence to 0 so the pipeline image-backs the (OCR) LaTeX.
    eq = _eq(r"\rho = 8\pi\nu^2/c^5")
    enrich_blocks([eq], _FakeGlyphs({1: None}))
    assert eq.extra["ocr"] is True and eq.confidence == 0.0


def test_garbled_equation_flagged_and_recovered():
    # Born-digital page: LaTeX disagrees with the clean text layer -> low
    # confidence, the reading captured as a hint, no OCR flag.
    eq = _eq(r"E ( \text {MR-AQC/CC} )")
    glyphs = _FakeGlyphs({1: _FakePC(text="E(MR-AQCC) − E(CASPT2) (4)")})
    enrich_blocks([eq], glyphs)
    assert eq.confidence is not None and eq.confidence < 0.9
    assert "ocr" not in eq.extra and eq.extra.get("text_layer")


def test_faithful_equation_trusted():
    eq = _eq(r"E ( M R - c c C A ) = E _ { 0 } ( M R - c c C A )")
    enrich_blocks([eq], _FakeGlyphs({1: _FakePC(text="E(MR-ccCA) = E0(MR-ccCA)")}))
    assert eq.confidence == 1.0 and "text_layer" not in eq.extra


def test_prose_religatured_against_vocab():
    p = Block(id="#/p", type=BlockType.PARAGRAPH, text="a di ff erent result", page=1, bbox=_BB)
    glyphs = _FakeGlyphs({1: _FakePC(scored=[])}, vocab={"different", "result"})
    enrich_blocks([p], glyphs)
    assert p.text == "a different result"  # ligature rejoined, no scripts to overlay


def test_garbage_prose_refilled_from_pdfium():
    # Engine text is symbol-font garbage (broken ToUnicode); the pdfium glyph layer
    # decodes the same bbox cleanly, so the block is refilled and stamped.
    p = Block(id="#/p", type=BlockType.PARAGRAPH,
              text="❆ ♣/a114❛❝/a116✐❝❛❧ ❣✉✐❞❡", page=1, bbox=_BB)
    glyphs = _FakeGlyphs({1: _FakePC(text="A practical guide\r\n")})
    enrich_blocks([p], glyphs)
    assert p.text == "A practical guide"
    assert p.extra["text_source"] == "pdfium"


def test_garbage_prose_kept_when_pdfium_also_garbage():
    # Both readings are garbage (a truly undecodable block): no swap, so the block
    # stays flagged illegible downstream rather than swapped for different garbage.
    garbage = "❆ ♣/a114❛❝/a116✐❝❛❧"
    p = Block(id="#/p", type=BlockType.PARAGRAPH, text=garbage, page=1, bbox=_BB)
    enrich_blocks([p], _FakeGlyphs({1: _FakePC(text="/a80/a114❡❢❛❝❡")}))
    assert p.text == garbage and "text_source" not in p.extra


def test_table_rebuilt_when_scripts_recovered():
    # A cell whose glyphs carry a superscript -> rebuild from cells, not the flat
    # engine markup. This is the table path that used to live inside the adapter.
    t = TableData(block_id="#/t", page=1, bbox=_BB, gfm="| n2 |", has_spanning_cells=False)
    raw = RawTable(
        cells=[RawCell(text="n2", bbox=_BB, row=0, col=0, row_span=1, col_span=1, header=False)],
        num_rows=1, num_cols=1,
    )
    glyphs = _FakeGlyphs({1: _FakePC(scored=[("n", None), ("2", "sup")])})
    enrich_tables([t], {"#/t": raw}, glyphs)
    assert "<sup>2</sup>" in t.gfm


def test_code_block_refilled_from_pdfium():
    # Docling labels a console transcript as code but its text is symbol-font garbage;
    # enrich re-reads it from pdfium with line breaks preserved.
    console = "\n".join([">>rnucleus", "Enter the atomic number:", ">>26"])
    b = Block(id="#/c", type=BlockType.CODE, text="❆/a114❝ ❣❛/a114❜❛❣❡", page=1, bbox=_BB)
    enrich_blocks([b], _FakeGlyphs({1: _FakePC(lines=console)}))
    assert b.text == console and "\n" in b.text


def test_console_prose_block_marked_preformatted():
    # A console transcript Docling mislabels as a paragraph: its banner lines mark it
    # preformatted, so enrich keeps the line structure for code-fence emission.
    console = "\n".join([
        "*****************************",
        "* RUN RNUCLEUS *",
        "*****************************",
        ">>rnucleus",
        "Enter the atomic number:",
    ])
    p = Block(id="#/p", type=BlockType.PARAGRAPH, text="flattened garbage", page=1, bbox=_BB)
    enrich_blocks([p], _FakeGlyphs({1: _FakePC(lines=console)}))
    assert p.extra.get("preformatted") is True and "\n" in p.text


def test_prose_with_pipes_not_preformatted():
    # A prose block must not be flagged just for containing '|' (bra-ket, abs value);
    # the pipe signal is table-only. No banner lines here -> stays prose.
    p = Block(id="#/p", type=BlockType.PARAGRAPH, text="x", page=1, bbox=_BB)
    pc = _FakePC(lines="the state |a| and |b| and |c|\nare normalized\nsee eq 3")
    enrich_blocks([p], _FakeGlyphs({1: pc}, vocab=set()))
    assert "preformatted" not in p.extra


def test_ascii_table_emitted_as_preformatted():
    # An ASCII-art "table" (literal pipe columns) Docling can't grid: keep it as
    # line-preserved text for a code fence, not a mangled GFM grid.
    ascii_tbl = "\n".join([
        "Configuration | Term | J | Level",
        "--------------|------|---|------",
        "2p6.3s2       | 1S   | 0 | 0",
        "3s.3p         | 3P*  | 0 | 233842",
    ])
    t = TableData(block_id="#/t", page=1, bbox=_BB, gfm="| mangled |", has_spanning_cells=False)
    enrich_tables([t], {}, _FakeGlyphs({1: _FakePC(lines=ascii_tbl)}))
    assert t.preformatted is not None and "Configuration | Term" in t.preformatted


def test_garbage_table_cell_refilled_from_pdfium():
    # A cell carrying the same broken font as the prose: refill from the pdfium
    # glyph layer and rebuild, even with no scripts to recover (the divergence the
    # rebuild needs is triggered by the garbage, not only by sub/superscripts).
    t = TableData(block_id="#/t", page=1, bbox=_BB, gfm="| ❆ ♣/a114❛❝/a116✐❝❛❧ |",
                  has_spanning_cells=False)
    raw = RawTable(
        cells=[RawCell(text="❆ ♣/a114❛❝/a116✐❝❛❧", bbox=_BB, row=0, col=0,
                       row_span=1, col_span=1, header=False)],
        num_rows=1, num_cols=1,
    )
    glyphs = _FakeGlyphs({1: _FakePC(text="A practical guide")})
    enrich_tables([t], {"#/t": raw}, glyphs)
    assert "A practical guide" in t.gfm and "❆" not in t.gfm


def test_refilled_cell_strips_column_rule_pipes():
    # This PDF draws table column rules as literal '|' glyphs the refill reads in;
    # strip those (else cells become \|-soup) but keep a content pipe like bra-ket.
    from pdf2md.enrich import _RULE_PIPE
    assert _RULE_PIPE.sub(" ", "| 3F* | 2 | 559600 |").split() == ["3F*", "2", "559600"]
    assert "|" in _RULE_PIPE.sub(" ", "|psi>")  # no surrounding space -> content, kept

    raw = RawTable(
        cells=[RawCell(text="❆/a114❝", bbox=_BB, row=0, col=0,
                       row_span=1, col_span=1, header=False)],
        num_rows=1, num_cols=1,
    )
    t = TableData(block_id="#/t", page=1, bbox=_BB, gfm="garbage", has_spanning_cells=False)
    enrich_tables([t], {"#/t": raw}, _FakeGlyphs({1: _FakePC(text="| 3F* |")}))
    assert "3F*" in t.gfm and "\\|" not in t.gfm


def test_table_falls_back_to_religatured_markup():
    # No structured cells -> keep the engine's rendering, ligature-repaired.
    t = TableData(block_id="#/t", page=1, bbox=_BB, gfm="a di ff erent cell")
    enrich_tables([t], {}, _FakeGlyphs({1: _FakePC(scored=[])}, vocab={"different"}))
    assert t.gfm == "a different cell"


def test_figure_caption_refilled_from_pdfium():
    # A caption in the broken font is dingbats; refill it from the glyph layer using
    # the caption's own bbox (not the picture's).
    f = FigureRef(block_id="#/f", page=1, bbox=_BB, caption="❋✐❣✉/a114❡ ✸✳✶", caption_bbox=_BB)
    enrich_figures([f], _FakeGlyphs({1: _FakePC(text="Figure 3.1: sequence")}))
    assert f.caption == "Figure 3.1: sequence"


def test_figure_caption_religatured():
    f = FigureRef(block_id="#/f", page=1, bbox=_BB, caption="a di ff erent fig")
    enrich_figures([f], _FakeGlyphs({1: _FakePC()}, vocab={"different"}))
    assert f.caption == "a different fig"


def test_prose_word_recall_recorded_when_complete():
    # Born-digital prose is compared against the glyph layer of its own region;
    # a faithful block matches every source word (case/order-insensitive).
    p = Block(id="#/p", type=BlockType.PARAGRAPH, text="the yield was 92 percent",
              page=1, bbox=_BB)
    record_recall([p], [], _FakeGlyphs({1: _FakePC(text="overall, The yield was 92 percent.")}))
    assert p.extra["glyph_word_recall"] == {"matched": 5, "total": 6, "strict": 5}


def test_prose_word_recall_detects_lost_words():
    p = Block(id="#/p", type=BlockType.PARAGRAPH, text="the yield was", page=1, bbox=_BB)
    record_recall([p], [], _FakeGlyphs({1: _FakePC(text="The yield was 92 percent")}))
    assert p.extra["glyph_word_recall"] == {"matched": 3, "total": 5, "strict": 3}


def test_word_recall_of_a_table_block_reads_its_cells():
    # A block whose content renders from cells has no text of its own. Measuring
    # `text` scored the whole printed region as lost (the GRASP2018 contents pages
    # read 0/266 while their tables were emitted in full), so the table's own
    # markup is the output side -- and its markup syntax is not source content.
    b = Block(id="#/tables/0", type=BlockType.OTHER, text="", page=1, bbox=_BB)
    t = TableData(block_id="#/tables/0", page=1, bbox=_BB,
                  gfm="| Running the tools | 169 |")
    glyphs = _FakeGlyphs({1: _FakePC(text="Running the tools 169")})
    record_recall([b], [t], glyphs)
    assert b.extra["glyph_word_recall"] == {"matched": 4, "total": 4, "strict": 4}


def test_word_recall_of_a_table_block_still_sees_dropped_characters():
    # The reason the fix matters: GRASP's contents table emitted "unningthetools"
    # for "Running the tools". Reading the cells reports that loss instead of
    # hiding it behind a 0/N that was wrong for an unrelated reason.
    b = Block(id="#/tables/0", type=BlockType.OTHER, text="", page=1, bbox=_BB)
    t = TableData(block_id="#/tables/0", page=1, bbox=_BB,
                  gfm="| unningthetools | 169 |")
    glyphs = _FakeGlyphs({1: _FakePC(text="Running the tools 169")})
    record_recall([b], [t], glyphs)
    assert b.extra["glyph_word_recall"] == {"matched": 1, "total": 4, "strict": 1}


def test_prose_word_recall_strips_script_tags_and_skips_empty_regions():
    from pdf2md.enrich import record_block_recall

    # A script tag becomes a boundary on both sides, because the source is read
    # script-split: `<sup>2</sup>nd` and the layer's `2nd` both give `2` + `nd`.
    p = Block(id="#/p", type=BlockType.PARAGRAPH, text="the <sup>2</sup>nd point",
              page=1, bbox=_BB)
    record_block_recall(p, _FakePC(text="The 2 nd point."))
    assert p.extra["glyph_word_recall"]["matched"] == 4

    empty = Block(id="#/q", type=BlockType.PARAGRAPH, text="orphan text", page=1, bbox=_BB)
    record_block_recall(empty, _FakePC(text=""))
    assert "glyph_word_recall" not in empty.extra


def test_recall_summary_aggregates_and_counts_low_blocks():
    from pdf2md.enrich import recall_summary

    def block(recall):
        return Block(id="#/b", type=BlockType.PARAGRAPH, text="x", page=1,
                     extra={"glyph_word_recall": recall} if recall else {})

    blocks = [
        block({"matched": 98, "total": 100, "strict": 98}),  # healthy
        block({"matched": 4, "total": 10, "strict": 4}),     # below the 0.90 floor
        block(None),                            # OCR page / empty region: not measured
    ]
    assert recall_summary(blocks) == {
        "blocks_measured": 2, "words_total": 110, "words_matched": 102,
        "low_recall_blocks": 1, "accent_damaged_blocks": 0,
    }


def test_numeric_accounting_canonicalizes_and_finds_missing():
    from pdf2md.enrich import numeric_accounting

    source = ("Table 3: −1,234.50 and 92; repeated 1,234 and 1,234; "
              "trailing dot 12.; ligature ﬁne x²")
    output = "-1234.50 ... 1234 ... 1234 ... 2"
    report = numeric_accounting(source, output)
    assert report["source_values"] == 7       # 3, -1234.50, 92, 1234, 1234, 12, 2
    assert report["distinct_source_values"] == 6
    assert report["conserved_values"] == 4    # minus sign, commas, trailing dot unified
    assert report["missing_values"] == 3      # 92, the table number 3, and 12
    assert {"value": "92", "count": 1} in report["missing_examples"]
    assert {"value": "12", "count": 1} in report["missing_examples"]


def test_numeric_accounting_survives_typeset_spacing_and_exponents():
    # The two real-world artifact families: rendered mantissas space out their
    # decimal point ("2 . 3"), and exponents ride as separate groups ("10 19"
    # once text_scriptsplit has split the glued layer form "1019"). Both sides
    # must tokenize to the same values.
    from pdf2md.enrich import numeric_accounting

    source = "cost 2.3 · 10 19 and rate d −0.5"
    output = "2 . 3 · 10 <sup>19</sup> and d^{ -0.5 }"
    report = numeric_accounting(source, output)
    assert report["source_values"] == 4       # 2.3, 10, 19, -0.5
    assert report["conserved_values"] == 4
    assert report["missing_values"] == 0

    # Dash style drift between the layer (en dash) and the markdown (hyphen)
    # must not turn a page range into a phantom negative.
    ranged = numeric_accounting("pages 152–159", "pages 152-159")
    assert ranged["missing_values"] == 0 and ranged["conserved_values"] == 2


def test_scriptsplit_separates_scripted_groups():
    from pdf2md.scripts import _scriptsplit_text

    def ch(text, l, b, r, t):
        return (text, float(l), float(b), float(r), float(t))

    # '10' on the baseline, a raised smaller '19' group after it.
    chars = [
        ch("1", 0, 0, 5, 10), ch("0", 5, 0, 10, 10),
        ch("1", 11, 6.2, 14, 9), ch("9", 15, 6.2, 18, 9),
        ch("x", 20, 0, 25, 10),
    ]
    assert _scriptsplit_text(chars).split() == ["10", "19", "x"]

    # A real space already separates base from script: no extra separator.
    spaced = [ch("1", 0, 0, 5, 10), ch(" ", 5, 0, 7, 10), ch("2", 8, 6.2, 11, 9)]
    assert _scriptsplit_text(spaced) == "1 2\n"


def test_recall_flags_separate_missing_words_from_lost_accents():
    from pdf2md.enrich import recall_review_flags

    def block(bid, recall):
        return Block(id=bid, type=BlockType.PARAGRAPH, text="x", page=4,
                     extra={"glyph_word_recall": recall})

    marked, informational = recall_review_flags([
        block("#/a", {"matched": 20, "total": 20, "strict": 20}),   # clean
        block("#/b", {"matched": 12, "total": 20, "strict": 12}),   # words gone
        block("#/c", {"matched": 20, "total": 20, "strict": 18}),   # accents gone
        block("#/d", {"matched": 19, "total": 20, "strict": 19}),   # within the floor
    ])

    assert [f.block_id for f in marked] == ["#/b"]
    assert marked[0].severity == "high"  # 8 words missing
    assert "8 of 20 source word(s)" in marked[0].reason
    assert "source page 4" in marked[0].marker_text

    # Accent damage is a different defect: the content is present and misspelled,
    # so it reaches review.json without putting a marker beside every surname.
    assert [f.block_id for f in informational] == ["#/c"]
    assert informational[0].disposition == "informational"
    assert "diacritics lost: 2 word(s)" in informational[0].reason


def test_recall_flags_read_a_bundle_written_before_strict_existed():
    from pdf2md.enrich import recall_review_flags, recall_summary

    old = Block(id="#/a", type=BlockType.PARAGRAPH, text="x", page=1,
                extra={"glyph_word_recall": {"matched": 20, "total": 20}})
    marked, informational = recall_review_flags([old])
    assert (marked, informational) == ([], [])
    assert recall_summary([old])["accent_damaged_blocks"] == 0


def test_recall_counts_a_line_broken_word_once():
    from pdf2md.enrich import record_block_recall

    # The layer breaks `structure` across a line with an undecodable soft hyphen;
    # the emitter rejoins it. Scoring that as two lost words is a metric bug.
    p = Block(id="#/p", type=BlockType.PARAGRAPH, text="the local structure holds",
              page=1, bbox=_BB)
    record_block_recall(p, _FakePC(text="the local struc￾\nture holds"))
    assert p.extra["glyph_word_recall"] == {"matched": 4, "total": 4, "strict": 4}


def test_recall_joins_a_word_the_glyph_layer_drew_in_two_runs():
    from pdf2md.enrich import record_block_recall

    # A styled capital splits `ReAct` across two source tokens with no hyphen to
    # join on. Scoring that as two lost words measures the draw order, not the
    # content — and it flagged the title block of a paper twice over.
    p = Block(id="#/p", type=BlockType.PARAGRAPH,
              text="ReAct synergizing reasoning and acting", page=1, bbox=_BB)
    record_block_recall(p, _FakePC(text="reac t synergizing reasoning and acting"))
    assert p.extra["glyph_word_recall"] == {"matched": 5, "total": 5, "strict": 5}


def test_recall_does_not_invent_a_join_the_output_never_had():
    from pdf2md.enrich import record_block_recall

    # The join is only made when the emitted text actually contains the result,
    # so two genuinely separate lost words stay lost.
    p = Block(id="#/p", type=BlockType.PARAGRAPH, text="the yield rose", page=1, bbox=_BB)
    record_block_recall(p, _FakePC(text="the yield rose over time"))
    assert p.extra["glyph_word_recall"] == {"matched": 3, "total": 5, "strict": 3}


def test_recall_is_not_claimed_where_two_blocks_claim_one_region():
    from pdf2md.enrich import recall_review_flags
    from pdf2md.schema import BBox

    def block(bid, box, recall):
        return Block(id=bid, type=BlockType.PARAGRAPH, text="x", page=1, bbox=box,
                     extra={"glyph_word_recall": recall})

    poor = {"matched": 12, "total": 20, "strict": 12}
    # Two boxes sharing most of their area: a word counted missing from one may
    # simply belong to the other, so neither recall is decidable.
    overlapping = [
        block("#/a", BBox(x0=0, y0=0, x1=100, y1=50), poor),
        block("#/b", BBox(x0=10, y0=5, x1=110, y1=45), dict(poor)),
    ]
    marked, informational = recall_review_flags(overlapping)
    assert marked == []
    assert {f.block_id for f in informational} == {"#/a", "#/b"}
    assert "region boundary" in informational[0].reason

    # Boxes that merely sit next to each other are measured as before.
    apart = [
        block("#/a", BBox(x0=0, y0=60, x1=100, y1=100), poor),
        block("#/b", BBox(x0=0, y0=0, x1=100, y1=50), dict(poor)),
    ]
    marked, _ = recall_review_flags(apart)
    assert {f.block_id for f in marked} == {"#/a", "#/b"}



class _Obj:
    def __init__(self, kind, pos=None):
        self.type, self._pos, self.raw = kind, pos, object()

    def get_pos(self):
        return self._pos


class _Page:
    """A page as `_detect_overlay` reads one: a size and a list of objects."""

    def __init__(self, objects, size=(200.0, 200.0)):
        self._objects, self._size = objects, size

    def get_size(self):
        return self._size

    def get_objects(self):
        return self._objects


def _page(*, image, text_objects, size=(200.0, 200.0)):
    objects = []
    if image:
        objects.append(_Obj(3, image))
    objects += [_Obj(1) for _ in range(text_objects)]
    return _Page(objects, size)


def test_a_scan_with_an_invisible_ocr_overlay_reads_as_having_no_text_layer(monkeypatch):
    from pdf2md import enrich

    monkeypatch.setattr(enrich, "_render_mode", lambda obj: 3)  # invisible
    index = enrich.GlyphIndex.__new__(enrich.GlyphIndex)
    assert index._detect_overlay(_page(image=(0, 0, 200, 200), text_objects=900))


def test_a_full_page_figure_with_labels_is_not_a_scan(monkeypatch):
    from pdf2md import enrich

    # Identical geometry — a full-page image with text over it — but the text is
    # drawn visibly, so it is the page's own content rather than an overlay.
    monkeypatch.setattr(enrich, "_render_mode", lambda obj: 0)  # fill
    index = enrich.GlyphIndex.__new__(enrich.GlyphIndex)
    assert index._detect_overlay(_page(image=(0, 0, 200, 200), text_objects=114)) is False


def test_a_page_without_a_full_page_image_is_never_a_scan(monkeypatch):
    from pdf2md import enrich

    monkeypatch.setattr(enrich, "_render_mode", lambda obj: 3)
    index = enrich.GlyphIndex.__new__(enrich.GlyphIndex)
    assert index._detect_overlay(_page(image=(10, 10, 60, 60), text_objects=900)) is False
    assert index._detect_overlay(_page(image=None, text_objects=900)) is False


def test_too_little_text_to_judge(monkeypatch):
    from pdf2md import enrich

    # A full-page plate with a caption under it is not a scan, and neither is a
    # blank page with a stamp on it.
    monkeypatch.setattr(enrich, "_render_mode", lambda obj: 3)
    index = enrich.GlyphIndex.__new__(enrich.GlyphIndex)
    assert index._detect_overlay(_page(image=(0, 0, 200, 200), text_objects=5)) is False


def _cell(text, col, x0, x1, row=0, span=1):
    return RawCell(text=text, bbox=BBox(x0=x0, y0=20.0, x1=x1, y1=10.0),
                   row=row, col=col, row_span=1, col_span=span, header=False)


def test_cell_read_box_widens_to_the_column_lane():
    # The engine draws a cell box inside the ink and `_region` keeps a glyph only
    # when its center is inside, so a tight box truncates the refill. Column 0's
    # lane is the union of its cells (10..40), which is wider than the tight cell
    # claiming 20..30 -- that union is what the individual box lacks.
    raw = RawTable(
        cells=[_cell("a", 0, 20.0, 30.0), _cell("b", 1, 60.0, 90.0),
               _cell("c", 0, 10.0, 40.0, row=1), _cell("d", 1, 60.0, 90.0, row=1)],
        num_rows=2, num_cols=2,
    )
    boxes = _cell_read_boxes(raw)
    assert (boxes[0].x0, boxes[0].x1) == (10.0, 40.0)
    # The y range is the cell's own: widening is horizontal only.
    assert (boxes[0].y0, boxes[0].y1) == (20.0, 10.0)


def test_cell_read_box_never_crosses_into_the_next_cell():
    # Widening stops at the neighbour even when the lane runs past it, so a
    # table of contents' leader dots stay out of the page-number cell.
    raw = RawTable(
        cells=[_cell("a", 0, 20.0, 30.0), _cell("b", 1, 35.0, 90.0),
               _cell("c", 0, 10.0, 80.0, row=1), _cell("d", 1, 85.0, 90.0, row=1)],
        num_rows=2, num_cols=2,
    )
    boxes = _cell_read_boxes(raw)
    assert boxes[0].x0 == 10.0        # widened left to the lane
    assert boxes[0].x1 == 35.0        # capped at the neighbour, not the lane's 80


def test_cell_read_boxes_do_not_claim_a_glyph_twice():
    # Left to right, each cell is bounded by where the previous one's *read* box
    # ended, so widening cannot reach ink another cell will also read.
    raw = RawTable(
        cells=[_cell("a", 0, 20.0, 30.0), _cell("b", 1, 32.0, 50.0),
               _cell("c", 0, 10.0, 45.0, row=1), _cell("d", 1, 31.0, 50.0, row=1)],
        num_rows=2, num_cols=2,
    )
    boxes = _cell_read_boxes(raw)
    assert boxes[0].x1 <= boxes[1].x0


def test_cell_read_box_falls_back_to_its_own_when_the_column_has_no_lane():
    # A spanning cell has no single-column lane; it keeps the engine's box.
    raw = RawTable(cells=[_cell("a", 0, 20.0, 30.0, span=2)], num_rows=1, num_cols=2)
    assert _cell_read_boxes(raw) == {}


def test_word_recall_splits_a_word_the_layer_glued():
    # The layer draws "Carlo calculations" with no space glyph between them, so
    # the region reads it as one token while the output has two. Unsplit, that
    # was one phantom loss plus one phantom extra -- the largest single cause of
    # false low-recall flags in the corpus (919 blocks -> 372 once handled).
    p = Block(id="#/p", type=BlockType.PARAGRAPH, text="monte carlo calculations ran",
              page=1, bbox=_BB)
    record_recall([p], [], _FakeGlyphs({1: _FakePC(text="monte carlocalculations ran")}))
    assert p.extra["glyph_word_recall"] == {"matched": 4, "total": 4, "strict": 4}


def test_word_recall_only_splits_into_words_the_output_really_has():
    q = Block(id="#/q", type=BlockType.PARAGRAPH, text="alpha gamma", page=1, bbox=_BB)
    record_recall([q], [], _FakeGlyphs({1: _FakePC(text="alpha betagamma")}))
    # `betagamma` would need `beta` and `gamma` adjacent in the output; only
    # `gamma` is there, so the word stays lost and a real drop is still reported.
    assert q.extra["glyph_word_recall"] == {"matched": 1, "total": 2, "strict": 1}


def test_a_list_item_number_is_not_reported_as_lost_text():
    # `emit` renders a list item as `- text`, so the printed number becomes list
    # structure rather than disappearing. Reported, but not as an action: it was
    # 81 of 90 numeral-only recall flags across the corpus.
    b = Block(id="#/l", type=BlockType.LIST, text="The properties of gases",
              page=1, bbox=_BB)
    record_recall([b], [], _FakeGlyphs({1: _FakePC(text="1 The properties of gases")}))
    assert b.extra["glyph_word_recall"]["list_marker_only"] is True
    marked, informational = recall_review_flags([b])
    assert marked == []
    assert len(informational) == 1 and "list marker" in informational[0].reason


def test_a_list_item_losing_a_word_is_still_an_action():
    # The exemption is only for the leading number. A missing word still counts.
    b = Block(id="#/l", type=BlockType.LIST, text="The properties", page=1, bbox=_BB)
    record_recall([b], [], _FakeGlyphs({1: _FakePC(text="1 The properties of gases")}))
    assert "list_marker_only" not in b.extra["glyph_word_recall"]
    marked, _ = recall_review_flags([b])
    assert len(marked) == 1
