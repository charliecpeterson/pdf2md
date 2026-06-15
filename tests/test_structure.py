from __future__ import annotations

from pdf2md.schema import Block, BlockType
from pdf2md.structure import build_structure


def _h(bid, text, page=1):
    return Block(id=bid, type=BlockType.HEADING, text=text, page=page)


def _p(bid, text, page=1):
    return Block(id=bid, type=BlockType.PARAGRAPH, text=text, page=page)


def test_heading_outline_nests_by_numbering():
    blocks = [_h("#/texts/0", "1 A"), _p("#/texts/1", "x"), _h("#/texts/2", "1.1 B")]
    s = build_structure(blocks, None, title="t", page_count=5)
    assert s.section_source == "heading_outline"
    assert s.split is False
    assert [c.title for c in s.root.children] == ["1 A"]
    assert [c.title for c in s.root.children[0].children] == ["1.1 B"]


def test_no_headings_is_single_file():
    blocks = [_p("#/texts/0", "just text")]
    s = build_structure(blocks, None, title="t", page_count=3)
    assert s.section_source == "none"
    assert s.split is False


def test_bookmarks_split_gated_on_page_count():
    blocks = [_h("#/texts/0", "A", 1), _h("#/texts/1", "B", 30)]
    bookmarks = [("A", 0, 0), ("B", 29, 0)]
    small = build_structure(blocks, bookmarks, title="t", page_count=10)
    assert small.section_source == "bookmarks"
    assert small.split is False  # paper-sized: stays single file
    big = build_structure(blocks, bookmarks, title="t", page_count=60)
    assert big.split is True  # book-sized: split per top-level bookmark
