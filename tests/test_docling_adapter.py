"""The docling adapter's pure translation — the cell-bbox coordinate flip, caption
bbox, and label→BlockType map — is the highest-churn code and the only load-bearing
logic the fast suite didn't cover. These helpers don't import docling at module level,
so they're testable with duck-typed fakes, no real Docling. They pin the contracts
documented in CLAUDE.md's gotchas (table-cell bboxes are TOPLEFT and must flip)."""

from __future__ import annotations

from pdf2md.engines.docling import _caption_bbox, _cell_bbox, _label_value
from pdf2md.engines.docling import _LABEL_MAP
from pdf2md.schema import BlockType


class _Origin:
    def __init__(self, name: str) -> None:
        self.name = name


class _BBox:
    def __init__(self, left, top, right, bot, origin) -> None:
        self.l, self.t, self.r, self.b = left, top, right, bot  # docling bbox attr names
        self.coord_origin = _Origin(origin)


class _Cell:
    def __init__(self, bbox) -> None:
        self.bbox = bbox


def test_cell_bbox_topleft_is_flipped_to_bottomleft():
    # TOPLEFT cell (t < b) on an 800-tall page -> bottom-left (y0 > y1) so enrich's
    # glyph lookups land on the cell, not the mirror-image region.
    bb = _cell_bbox(_Cell(_BBox(10, 100, 50, 120, "TOPLEFT")), page_height=800)
    assert (bb.x0, bb.x1) == (10, 50)
    assert bb.y0 == 700 and bb.y1 == 680  # 800-100, 800-120 -> y0 > y1


def test_cell_bbox_bottomleft_passthrough():
    bb = _cell_bbox(_Cell(_BBox(10, 120, 50, 100, "BOTTOMLEFT")), page_height=800)
    assert (bb.x0, bb.y0, bb.x1, bb.y1) == (10, 120, 50, 100)


def test_cell_bbox_none_and_no_page_height():
    assert _cell_bbox(_Cell(None), 800) is None
    # no page height -> can't flip a TOPLEFT bbox, pass it through unchanged
    bb = _cell_bbox(_Cell(_BBox(10, 100, 50, 120, "TOPLEFT")), page_height=None)
    assert bb.y0 == 100 and bb.y1 == 120


def test_label_map_contracts():
    # A regression that remaps any of these (e.g. formula -> something other than
    # EQUATION) silently corrupts every doc; pin the load-bearing keys.
    assert _LABEL_MAP["formula"] is BlockType.EQUATION
    assert _LABEL_MAP["section_header"] is BlockType.HEADING
    assert _LABEL_MAP["table"] is BlockType.TABLE
    assert _LABEL_MAP["picture"] is BlockType.FIGURE
    assert _LABEL_MAP["code"] is BlockType.CODE


def test_label_value_reads_enum_value():
    class _L:
        value = "formula"

    class _Item:
        label = _L()

    assert _label_value(_Item()) == "formula"


def test_caption_bbox_resolves_and_passes_through():
    # captions are prov bboxes (BOTTOMLEFT), not flipped.
    class _Prov:
        def __init__(self, bbox):
            self.bbox = bbox

    class _Item:
        def __init__(self, prov):
            self.prov = prov

    class _Ref:
        def __init__(self, item):
            self._item = item

        def resolve(self, doc):
            return self._item

    class _Pic:
        captions = [_Ref(_Item([_Prov(_BBox(5, 80, 60, 70, "BOTTOMLEFT"))]))]

    bb = _caption_bbox(doc=None, pic=_Pic())
    assert (bb.x0, bb.y0, bb.x1, bb.y1) == (5, 80, 60, 70)
