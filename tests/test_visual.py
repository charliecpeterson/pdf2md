"""Figure cleanup keeps scientific content while removing labelled publisher UI."""

from __future__ import annotations

from pdf2md.schema import BBox, Block, BlockType, FigureLabels, FigureRef
from pdf2md.visual import associate_figure_captions, clean_figure_structure


def _figure(identifier: str, bbox: BBox, **kwargs) -> FigureRef:
    return FigureRef(identifier, 1, bbox, **kwargs)


def _block(identifier: str, bbox: BBox, text: str = "") -> Block:
    return Block(identifier, BlockType.FIGURE, text, 1, bbox)


def test_figure_owns_caption_block_with_the_same_source_bbox():
    caption_bbox = BBox(50, 100, 300, 70)
    figure = _figure(
        "figure",
        BBox(50, 400, 300, 110),
        caption="FIG. 1. Plain caption",
        caption_bbox=caption_bbox,
    )
    caption = Block(
        "caption",
        BlockType.CAPTION,
        "FIG. 1. Caption with E<sub>corr</sub> markup.",
        1,
        caption_bbox,
    )
    unrelated = Block(
        "other-caption",
        BlockType.CAPTION,
        "FIG. 2. Different figure.",
        1,
        BBox(50, 60, 300, 30),
    )

    assert associate_figure_captions([caption, unrelated], [figure]) == 1
    assert figure.caption == "FIG. 1. Caption with E<sub>corr</sub> markup."
    assert caption.extra["figure_caption_of"] == "figure"
    assert "figure_caption_of" not in unrelated.extra


def test_clean_figure_structure_removes_labelled_journal_furniture():
    logo = _figure(
        "logo",
        BBox(10, 100, 100, 80),
        labels=FigureLabels("AIP Publishing", 0.5, "engine text"),
    )
    update = _figure("update", BBox(120, 701, 138, 684))
    scientific = _figure(
        "science", BBox(50, 740, 300, 390), caption="FIG. 1. Carbon atom"
    )
    blocks = [
        _block("logo", logo.bbox),
        _block("update", update.bbox),
        Block("text", BlockType.PARAGRAPH, "Checkfor updates", 1, BBox(140, 698, 205, 688)),
        _block("science", scientific.bbox),
    ]
    figures = [logo, update, scientific]

    counts = clean_figure_structure(blocks, figures)

    assert counts == {
        "furniture_removed": 2,
        "panels_merged": 0,
        "fragments_merged": 0,
        "graphic_components_included": 0,
        "panel_headings_absorbed": 0,
    }
    assert [figure.block_id for figure in figures] == ["science"]
    assert [block.id for block in blocks] == ["text", "science"]


def test_clean_figure_structure_merges_explicit_captioned_panels():
    left = _figure(
        "left",
        BBox(173, 698, 240, 573),
        labels=FigureLabels("MatMul", 0.5, "engine text"),
    )
    right = _figure(
        "right",
        BBox(347, 721, 467, 554),
        caption=(
            "Figure 2: (left) Scaled Dot-Product Attention. "
            "(right) Multi-Head Attention."
        ),
        caption_bbox=BBox(108, 516, 504, 497),
        labels=FigureLabels("Multi-Head Attention", 0.5, "engine text"),
    )
    blocks = [
        Block(
            "left-title",
            BlockType.HEADING,
            "Scaled Dot-Product Attention",
            1,
            BBox(148, 720, 266, 711),
        ),
        _block("left", left.bbox),
        _block("right", right.bbox),
    ]
    figures = [left, right]

    counts = clean_figure_structure(blocks, figures)

    assert counts == {
        "furniture_removed": 0,
        "panels_merged": 1,
        "fragments_merged": 0,
        "graphic_components_included": 0,
        "panel_headings_absorbed": 1,
    }
    assert [figure.block_id for figure in figures] == ["right"]
    assert [block.id for block in blocks] == ["right"]
    assert right.bbox == BBox(148, 721, 467, 554)
    assert right.labels.text == "MatMul\nMulti-Head Attention"


def test_clean_figure_structure_keeps_uncaptioned_scientific_figure():
    scientific = _figure(
        "science",
        BBox(50, 740, 300, 390),
        labels=FigureLabels("Energy radius Carbon atom", 0.5, "engine text"),
    )
    blocks = [_block("science", scientific.bbox)]
    figures = [scientific]

    counts = clean_figure_structure(blocks, figures)

    assert counts == {
        "furniture_removed": 0,
        "panels_merged": 0,
        "fragments_merged": 0,
        "graphic_components_included": 0,
        "panel_headings_absorbed": 0,
    }
    assert figures == [scientific]


def test_clean_figure_structure_merges_explicit_continuation_fragments():
    top = _figure("top", BBox(85, 664, 160, 617))
    middle = _figure("middle", BBox(90, 533, 410, 511))
    lower = _figure("lower", BBox(85, 259, 290, 220))
    anchor = _figure(
        "anchor",
        BBox(85, 127, 218, 87),
        caption="Figure 28. Sample Case Output (Contd).",
        caption_bbox=BBox(116, 78, 508, 62),
    )
    blocks = [
        Block(
            "title",
            BlockType.PARAGRAPH,
            "CASE IDENTIFICATION",
            1,
            BBox(84, 693, 405, 679),
        ),
        Block(
            "scan-note",
            BlockType.HEADING,
            "ORIGINAL PAGE IS OF POOR QUALITY",
            1,
            BBox(250, 715, 356, 701),
        ),
        *[_block(figure.block_id, figure.bbox) for figure in (top, middle, lower, anchor)],
    ]
    figures = [top, middle, lower, anchor]

    counts = clean_figure_structure(blocks, figures)

    assert counts == {
        "furniture_removed": 0,
        "panels_merged": 0,
        "fragments_merged": 3,
        "graphic_components_included": 0,
        "panel_headings_absorbed": 0,
    }
    assert figures == [anchor]
    assert anchor.bbox == BBox(84, 693, 410, 87)
    assert [block.id for block in blocks] == ["title", "scan-note", "anchor"]


def test_clean_figure_structure_includes_adjacent_graphical_abstract_components():
    graphic = _figure("graphic", BBox(313, 713, 551, 477))
    title = Block(
        "title", BlockType.PARAGRAPH, "optimal orbitals", 1, BBox(106, 674, 298, 663)
    )
    equation = Block(
        "equation", BlockType.EQUATION, "x = 0", 1, BBox(254, 608, 282, 581)
    )
    subtitle = Block(
        "subtitle", BlockType.PARAGRAPH, "adaptive basis", 1, BBox(116, 536, 288, 522)
    )
    graphic_block = _block("graphic", graphic.bbox)
    blocks = [title, equation, subtitle, graphic_block]
    figures = [graphic]

    counts = clean_figure_structure(blocks, figures)

    assert counts == {
        "furniture_removed": 0,
        "panels_merged": 0,
        "fragments_merged": 0,
        "graphic_components_included": 3,
        "panel_headings_absorbed": 0,
    }
    assert graphic.bbox == BBox(106, 713, 551, 477)
    assert blocks == [title, equation, subtitle, graphic_block]
