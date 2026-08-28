from __future__ import annotations

import pytest

from pdf2md.outline import heading_depth, section_kind
from pdf2md.schema import Block, BlockType, SectionKind


def _h(text: str, **extra) -> Block:
    return Block(id="x", type=BlockType.HEADING, text=text, page=1, extra=extra)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1 Introduction", 1),
        ("3.5 Positional Encoding", 2),
        ("3.2.1 Scaled Dot-Product Attention", 3),
        ("Abstract", 1),
        ("Appendix A", 1),
    ],
)
def test_heading_depth_from_numbering(text, expected):
    assert heading_depth(_h(text)) == expected


def test_heading_depth_falls_back_to_docling_level():
    assert heading_depth(_h("Unnumbered", level=3)) == 3


def test_heading_depth_caps_at_six():
    assert heading_depth(_h("1.2.3.4.5.6.7 Deep")) == 6


def test_section_kind_appendix():
    assert section_kind("Appendix B", 1) == SectionKind.APPENDIX
    assert section_kind("Introduction", 1) == SectionKind.SECTION
