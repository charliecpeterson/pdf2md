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
    assert big.split_depth == 1


def test_bookmarks_split_below_explicit_part_layer():
    blocks = [
        _h("part-1", "Part I", 1),
        _h("chapter-1", "Chapter 1", 3),
        _h("part-2", "Part II", 21),
        _h("chapter-2", "Chapter 2", 23),
    ]
    bookmarks = [
        ("I First part", 0, 0),
        ("First chapter", 2, 1),
        ("II Second part", 20, 0),
        ("Second chapter", 22, 1),
    ]

    structure = build_structure(blocks, bookmarks, title="t", page_count=60)

    assert structure.split is True
    assert structure.split_depth == 2


def test_bookmarks_do_not_infer_part_layer_from_nested_chapters():
    blocks = [_h("chapter-1", "Chapter 1", 1), _h("chapter-2", "Chapter 2", 30)]
    bookmarks = [
        ("Chapter 1", 0, 0),
        ("Section 1.1", 2, 1),
        ("Chapter 2", 29, 0),
        ("Section 2.1", 31, 1),
    ]

    structure = build_structure(blocks, bookmarks, title="t", page_count=60)

    assert structure.split_depth == 1


def test_mixed_bookmarks_sort_by_page_and_expand_only_chapter_containers():
    blocks = [
        _h("notation", "Notation", 8),
        _h("contents", "Contents", 10),
        _h("part", "I: Foundations", 16),
        _h("chapter-1", "1 Introduction", 18),
        _h("chapter-2", "2 Theory", 30),
        _h("index", "Index", 50),
        _h("index-a", "A", 50),
    ]
    bookmarks = [
        ("Contents", 9, 0),
        ("Notation", 7, 0),
        ("I: Foundations", 15, 0),
        ("1 Introduction", 17, 1),
        ("2 Theory", 29, 1),
        ("Index", 49, 0),
        ("A", 49, 1),
    ]

    structure = build_structure(blocks, bookmarks, title="Book", page_count=60)

    assert [section.title for section in structure.root.children] == [
        "Notation", "Contents", "I: Foundations", "Index",
    ]
    assert structure.split_depth == 2


def test_coarse_part_bookmarks_fall_back_to_numbered_heading_chapters():
    blocks = [
        _h("part-1", "Part I", 1),
        _h("chapter-1", "1 First chapter", 3),
        _p("body-1", "first", 4),
        _h("chapter-2", "2 Second chapter", 20),
        _p("body-2", "second", 21),
        _h("back", "Bibliography", 40),
    ]
    bookmarks = [("I Main text", 0, 0), ("Bibliography", 39, 0)]

    structure = build_structure(blocks, bookmarks, title="Book", page_count=60)
    part = structure.root.children[0]

    assert structure.split_depth == 2
    assert part.block_ids == ["part-1"]
    assert [(child.id, child.title, child.block_ids) for child in part.children] == [
        ("chapter-1", "1 First chapter", ["chapter-1", "body-1"]),
        ("chapter-2", "2 Second chapter", ["chapter-2", "body-2"]),
    ]
