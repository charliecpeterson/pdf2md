"""Marker's JSON block tree translates to pdf2md types.

Fixtures follow the shape a real `marker_single --output_format json` run emits:
a Document whose children are pages, each page carrying blocks with `html`,
`polygon` and `bbox` in points with a top-left origin.
"""

from __future__ import annotations

import pytest

from pdf2md.engines.marker import _flatten, _text, _translate
from pdf2md.schema import BlockType


def _page(children, width=600.0, height=800.0):
    return {
        "block_type": "Document",
        "children": [{
            "id": "/page/0",
            "block_type": "Page",
            "html": "",
            "bbox": [0.0, 0.0, width, height],
            "polygon": [[0, 0], [width, 0], [width, height], [0, height]],
            "children": children,
        }],
    }


def _block(block_type, html, bbox, block_id=None, children=None):
    item = {
        "id": block_id or f"/page/0/{block_type}/0",
        "block_type": block_type,
        "html": html,
        "bbox": list(bbox),
        "polygon": [],
    }
    if children is not None:
        item["children"] = children
    return item


def test_blocks_carry_type_text_and_bottom_left_geometry():
    document = _page([
        _block("SectionHeader", "<h2>Basis sets</h2>", [86.0, 69.0, 508.0, 105.0]),
        _block("Text", "<p>Energy is conserved.</p>", [78.0, 142.0, 206.0, 223.0]),
        _block("PageFooter", "<p>12</p>", [80.0, 770.0, 120.0, 785.0]),
    ])

    result = _translate(document, "marker, version 1.2.3")

    assert [b.type for b in result.blocks] == [
        BlockType.HEADING, BlockType.PARAGRAPH, BlockType.PAGE_FOOTER,
    ]
    assert [b.text for b in result.blocks] == ["Basis sets", "Energy is conserved.", "12"]
    assert result.page_sizes == {1: (600.0, 800.0)}
    assert result.engine_versions["marker"] == "marker, version 1.2.3"
    assert all(b.engine == "marker" for b in result.blocks)

    # Top-left in, bottom-left out: y0 above y1, and both measured from the bottom.
    heading = result.blocks[0].bbox
    assert (heading.x0, heading.y0, heading.x1, heading.y1) == (86.0, 731.0, 508.0, 695.0)
    assert heading.y0 > heading.y1


def test_page_header_and_footer_reach_the_labels_emit_already_strips():
    """Docling assigns these to zero blocks, so the boilerplate strip never fires.

    Marker labels them, which is the whole reason the mapping is worth pinning.
    """
    document = _page([
        _block("PageHeader", "<p>Journal of Chemical Physics</p>", [10.0, 10.0, 580.0, 24.0]),
        _block("Text", "<p>Body.</p>", [10.0, 40.0, 580.0, 90.0]),
        _block("PageFooter", "<p>Page 3</p>", [10.0, 760.0, 580.0, 780.0]),
    ])

    types = [b.type for b in _translate(document).blocks]

    assert types == [BlockType.PAGE_HEADER, BlockType.PARAGRAPH, BlockType.PAGE_FOOTER]


def test_a_table_becomes_gfm_and_registers_no_raw_cells():
    """Marker's JSON renderer flattens TableCell into the table's HTML.

    So tables arrive as markup and this adapter supplies no raw_tables, exactly as
    the MinerU adapter does. Pinned because the per-cell glyph verification in
    enrich and table_audit depends on that dict being populated, and a future
    change that starts emitting cells should have to update this test.
    """
    html = (
        "<table><tr><th>Atom</th><th>Energy</th></tr>"
        "<tr><td>He</td><td>-2.90</td></tr></table>"
    )
    document = _page([_block("Table", html, [50.0, 100.0, 550.0, 300.0])])

    result = _translate(document)

    assert [b.type for b in result.blocks] == [BlockType.TABLE]
    assert result.blocks[0].text == ""
    assert len(result.tables) == 1
    table = result.tables[0]
    assert "| Atom | Energy |" in table.gfm
    assert "| He | -2.90 |" in table.gfm
    assert table.has_spanning_cells is False
    assert table.html is None
    assert result.raw_tables == {}


def test_a_spanning_table_keeps_its_html_fallback():
    html = (
        "<table><tr><th>A</th><th>B</th></tr>"
        '<tr><td rowspan="2">1</td><td>x</td></tr><tr><td>y</td></tr></table>'
    )
    document = _page([_block("Table", html, [50.0, 100.0, 550.0, 300.0])])

    table = _translate(document).tables[0]

    assert table.has_spanning_cells is True
    assert table.html == html


def test_a_figure_becomes_a_figure_ref_with_its_printed_text_as_labels():
    document = _page([
        _block("Figure", "<p>Figure 2. Potential energy surface</p>",
               [40.0, 200.0, 400.0, 500.0]),
    ])

    result = _translate(document)

    assert [b.type for b in result.blocks] == [BlockType.FIGURE]
    assert result.blocks[0].text == ""
    assert len(result.figures) == 1
    figure = result.figures[0]
    assert figure.labels is not None
    assert figure.labels.text == "Figure 2. Potential energy surface"
    assert figure.bbox.y0 == 600.0 and figure.bbox.y1 == 300.0


def test_a_group_is_replaced_by_its_members_but_a_table_group_is_not():
    """A group carries both its members and its own assembled HTML.

    Emitting both would duplicate the content; a table or figure group is instead
    the unit pdf2md crops, so it stays whole.
    """
    document = _page([
        _block("ListGroup", "<ul><li>one</li><li>two</li></ul>", [10, 10, 100, 60],
               children=[
                   _block("ListItem", "<li>one</li>", [10, 10, 100, 30], "/page/0/ListItem/1"),
                   _block("ListItem", "<li>two</li>", [10, 35, 100, 60], "/page/0/ListItem/2"),
               ]),
        _block("TableGroup", "<table><tr><td>v</td></tr></table>", [10, 70, 100, 120],
               children=[_block("Table", "<table><tr><td>v</td></tr></table>",
                                [10, 70, 100, 120], "/page/0/Table/3")]),
    ])

    result = _translate(document)

    assert [b.type for b in result.blocks] == [
        BlockType.LIST, BlockType.LIST, BlockType.TABLE,
    ]
    assert [b.text for b in result.blocks[:2]] == ["one", "two"]
    assert len(result.tables) == 1


def test_an_unknown_block_type_lands_on_other_rather_than_vanishing():
    document = _page([_block("SomeNewMarkerType", "<p>kept</p>", [10, 10, 100, 30])])

    blocks = _translate(document).blocks

    assert [b.type for b in blocks] == [BlockType.OTHER]
    assert blocks[0].text == "kept"


@pytest.mark.parametrize("html,expected", [
    ("<p>one</p><p>two</p>", "one\ntwo"),
    ("a<br>b", "a\nb"),
    ("<p>  spaced   out  </p>", "spaced out"),
    ("<h2><b>Bold</b> <b>title</b></h2>", "Bold title"),
    ("", ""),
])
def test_block_html_reduces_to_visible_text(html, expected):
    assert _text(html) == expected


def test_flatten_leaves_a_childless_block_alone():
    item = _block("Text", "<p>x</p>", [0, 0, 1, 1])
    assert _flatten([item]) == [item]
