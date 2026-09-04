"""Marker's JSON block tree translates to pdf2md types.

Fixtures follow the shape a real `marker_single --output_format json` run emits:
a Document whose children are pages, each page carrying blocks with `html`,
`polygon` and `bbox` in points with a top-left origin.
"""

from __future__ import annotations

import json
from pathlib import Path

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


def test_a_group_is_replaced_by_its_members_including_a_table_group():
    """A group's own html is content-refs, never the content.

    Reading a TableGroup as the unit handed the table converter
    `<content-ref src=.../>` and produced an empty grid, losing 240 table tests
    that Marker itself passes. The group is always replaced by its members.
    """
    document = _page([
        _block("ListGroup", "<ul><li>one</li><li>two</li></ul>", [10, 10, 100, 60],
               children=[
                   _block("ListItem", "<li>one</li>", [10, 10, 100, 30], "/page/0/ListItem/1"),
                   _block("ListItem", "<li>two</li>", [10, 35, 100, 60], "/page/0/ListItem/2"),
               ]),
        _block("TableGroup",
               "<content-ref src='/page/0/Caption/2'></content-ref>"
               "<content-ref src='/page/0/Table/3'></content-ref>",
               [10, 70, 100, 130],
               children=[
                   _block("Caption", "<p><b>Table 1:</b> Results.</p>",
                          [10, 70, 100, 85], "/page/0/Caption/2"),
                   _block("Table",
                          "<table><tr><th>k</th></tr><tr><td>v</td></tr></table>",
                          [10, 90, 100, 130], "/page/0/Table/3"),
               ]),
    ])

    result = _translate(document)

    assert [b.type for b in result.blocks] == [
        BlockType.LIST, BlockType.LIST, BlockType.CAPTION, BlockType.TABLE,
    ]
    assert [b.text for b in result.blocks[:2]] == ["one", "two"]
    assert result.blocks[2].text == "Table 1: Results."
    assert len(result.tables) == 1
    assert "| k |" in result.tables[0].gfm
    assert "| v |" in result.tables[0].gfm


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


def test_inline_maths_keeps_its_delimiters():
    """Marker wraps LaTeX in <math>; stripping the tag alone loses the mathematics.

    Without the delimiters `$Q(1)^n$` reaches the output as `Q(1)^n`, which no
    reader can tell from prose -- and the glyph script overlay then rewrote it as
    `Q(1)^<sup>n</sup>`. That cost nearly all of Marker's arxiv_math advantage.
    """
    document = _page([
        _block("Text",
               "<p>supertorus <math>Q(1)^n</math> and variety "
               "<math>\\mathbb{P}^n</math>.</p>",
               [10, 10, 500, 60]),
        _block("Text", '<p><math display="block">E = mc^2</math></p>', [10, 70, 500, 120]),
    ])

    texts = [b.text for b in _translate(document).blocks]

    assert texts[0] == "supertorus $Q(1)^n$ and variety $\\mathbb{P}^n$."
    assert texts[1] == "$$E = mc^2$$"


def test_a_table_cell_holding_maths_keeps_it():
    html = (
        "<table><thead><tr><th>Path</th><th><math>\\beta</math></th></tr></thead>"
        "<tbody><tr><td>JS-BO</td><td>0.31</td></tr></tbody></table>"
    )
    document = _page([_block("Table", html, [10, 10, 500, 200])])

    gfm = _translate(document).tables[0].gfm

    assert "$\\beta$" in gfm


# Hand-built fixtures encode the author's model of Marker, which is exactly what
# was wrong: the TableGroup fixture above originally carried real table markup in
# its `html`, so it passed while production received `<content-ref>` stubs and
# emitted an empty grid. These drive the translator from output a real
# `marker_single --output_format json` run produced, with base64 images elided.
_FIXTURES = Path(__file__).parent / "fixtures" / "marker"


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


def test_real_output_table_group_yields_the_table_not_a_content_ref():
    result = _translate(_fixture("marker_table_group.json"))

    assert len(result.tables) == 2
    for table in result.tables:
        assert table.gfm, "a TableGroup whose html is content-refs must still yield a grid"
        assert "content-ref" not in table.gfm
    assert "| Variable | Mean |" in result.tables[0].gfm.replace("  ", " ")
    # The caption is a sibling of the table inside the group, and must survive it.
    captions = [b.text for b in result.blocks if b.type is BlockType.CAPTION]
    assert any(c.startswith("Table 1:") for c in captions)


def test_real_output_keeps_inline_maths_delimited():
    result = _translate(_fixture("marker_inline_math.json"))

    prose = [b.text for b in result.blocks if b.type is BlockType.PARAGRAPH]
    delimited = [t for t in prose if "$" in t]
    assert delimited, "this page is mathematics; none of it reached the output as maths"
    joined = "\n".join(delimited)
    assert "$" in joined and "<math>" not in joined
    # Every delimiter opened is closed: an odd count means a span leaked into prose.
    assert joined.count("$") % 2 == 0


def test_real_output_detects_page_furniture_and_transcribes_none_of_it():
    """Marker marks every header and footer region and reads none of them.

    Measured over the benchmark: 1,540 PageHeader and 1,028 PageFooter regions,
    every one with empty html. So its advantage on the `absent` class comes from
    not reading furniture at all, not from labelling it for emit to strip -- and
    those regions must not reach the output as empty blocks.
    """
    raw = _fixture("marker_table_and_figure.json")
    furniture = [b for pg in raw["children"] for b in pg.get("children") or []
                 if b["block_type"] in ("PageHeader", "PageFooter")]
    assert furniture, "this fixture is chosen for its furniture"
    assert all(not (b.get("html") or "").strip() for b in furniture)

    result = _translate(raw)

    kinds = [b.type for b in result.blocks]
    assert BlockType.PAGE_HEADER not in kinds
    assert BlockType.PAGE_FOOTER not in kinds
    assert len(result.figures) == 1
    assert len(result.tables) == 1
    assert result.tables[0].gfm.count("\n") >= 3


def test_real_output_geometry_is_inside_the_page():
    """A sign error here is invisible in a hand-built fixture and fatal in production."""
    for name in ("marker_inline_math.json", "marker_table_group.json",
                 "marker_table_and_figure.json"):
        result = _translate(_fixture(name))
        for block in result.blocks:
            if block.bbox is None:
                continue
            width, height = result.page_sizes[block.page]
            assert 0 <= block.bbox.x0 <= width + 1, f"{name}: {block.id}"
            assert 0 <= block.bbox.y1 <= height + 1, f"{name}: {block.id}"
            assert block.bbox.y0 >= block.bbox.y1, f"{name}: {block.id} not bottom-left"


def test_a_region_marker_transcribed_nothing_into_is_not_a_block():
    """Admitting it makes the coverage audit report content dropped where the
    engine never offered any: 163 such blocks across seven documents, against none
    from Docling, and every one of them empty. A table or figure still passes,
    because its content is the crop rather than its text."""
    document = _page([
        _block("PageHeader", "", [10, 10, 500, 24]),
        _block("Text", "", [10, 40, 500, 60]),
        _block("Text", "<p>real content</p>", [10, 70, 500, 100]),
        _block("Figure", "", [10, 110, 300, 400]),
        _block("Table", "<table><tr><td>v</td></tr></table>", [10, 410, 300, 500]),
    ])

    result = _translate(document)

    assert [b.type for b in result.blocks] == [
        BlockType.PARAGRAPH, BlockType.FIGURE, BlockType.TABLE,
    ]
    assert result.blocks[0].text == "real content"
    assert len(result.figures) == 1
    assert len(result.tables) == 1


def test_a_display_equation_block_carries_bare_latex():
    """emit adds the `$$` fences, so the adapter must not.

    Marker wraps a display equation in <math display="block">, which becomes `$$`
    here; leaving it double-wrapped all 1,895 equations in the corpus and tripped
    the unbalanced-LaTeX invariant. Inline maths inside prose keeps its delimiters,
    because there the block is not the equation.
    """
    document = _page([
        _block("Equation", '<math display="block">E = mc^2</math>', [10, 10, 500, 60]),
        _block("Text", "<p>where <math>m</math> is mass</p>", [10, 70, 500, 100]),
    ])

    blocks = _translate(document).blocks

    assert blocks[0].type is BlockType.EQUATION
    assert blocks[0].text == "E = mc^2"
    assert blocks[1].text == "where $m$ is mass"
