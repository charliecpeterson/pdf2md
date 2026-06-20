"""The enrichment layer is engine-agnostic, so it's testable with a fake glyph
source — no Docling, no real PDF. These pin the orchestration (which block gets
what) that used to live untested inside the Docling adapter."""

from __future__ import annotations

from pdf2md.enrich import enrich_blocks
from pdf2md.schema import BBox, Block, BlockType

_BB = BBox(x0=0, y0=10, x1=10, y1=0)


class _FakePC:
    empty = False

    def __init__(self, text: str = "", scored=None, disorder: float = 0.0) -> None:
        self._text, self._scored, self._disorder = text, scored or [], disorder

    def text_region(self, bbox) -> str:
        return self._text

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
