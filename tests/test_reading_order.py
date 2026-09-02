"""Reading-order verification: pure geometry against emission position, so
synthetic block boxes pin it without a PDF, an engine, or a rendered page."""

from __future__ import annotations

from pdf2md.reading_order import (
    column_starts,
    ordinal_findings,
    page_findings,
    reading_order_flags,
)
from pdf2md.schema import BBox, Block, BlockType

# A two-column page: columns start at x=60 and x=324, each 240pt wide.
LEFT, RIGHT, WIDTH = 60.0, 324.0, 240.0


def block(bid: str, x: float, top: float, width: float = WIDTH,
          kind: BlockType = BlockType.PARAGRAPH, page: int = 1) -> Block:
    return Block(id=bid, type=kind, text="body text", page=page,
                 bbox=BBox(x0=x, y0=top - 20.0, x1=x + width, y1=top))


def emitted(*ids: str) -> dict[str, int]:
    """Where each block landed in the Markdown, in emission order."""
    return {bid: i * 100 for i, bid in enumerate(ids)}


def spans(*ids: str) -> dict[str, dict[str, object]]:
    """The same, in the emission-index shape `reading_order_flags` consumes."""
    return {
        bid: {"start": start, "markdown": "document.md"}
        for bid, start in emitted(*ids).items()
    }


def two_column_page() -> list[Block]:
    return [
        block("#/a", LEFT, 700.0), block("#/b", LEFT, 600.0),
        block("#/c", LEFT, 500.0), block("#/d", RIGHT, 700.0),
        block("#/e", RIGHT, 600.0), block("#/f", RIGHT, 500.0),
    ]


def test_column_starts_come_from_where_blocks_begin():
    assert column_starts([b.bbox for b in two_column_page()]) == [LEFT, RIGHT]


def test_a_full_width_title_does_not_erase_the_columns_beneath_it():
    # The title crosses the gutter. A corridor search sees one column and calls
    # every body block misordered; left edges still say there are two.
    page = [block("#/title", LEFT, 760.0, width=500.0), *two_column_page()]
    assert column_starts([b.bbox for b in page]) == [LEFT, RIGHT]


def test_column_major_order_passes():
    page = two_column_page()
    assert page_findings(page, emitted("#/a", "#/b", "#/c", "#/d", "#/e", "#/f")) is None


def test_interleaved_columns_are_reported():
    # Reading across the page instead of down each column: every word survives
    # and every number is conserved, so nothing else here would notice.
    page = two_column_page()
    finding = page_findings(page, emitted("#/a", "#/d", "#/b", "#/e", "#/c", "#/f"))
    assert finding is not None
    assert finding["columns"] == 2
    # Two blocks moved is the smallest edit that restores the printed order; the
    # four blocks involved in an inverted pair would overstate one mistake.
    assert finding["misplaced"] == 2
    assert set(finding["out_of_place"]) <= {"#/b", "#/c", "#/d", "#/e"}


def test_two_swapped_blocks_are_reported_as_one_move():
    page = two_column_page()
    finding = page_findings(page, emitted("#/a", "#/c", "#/b", "#/d", "#/e", "#/f"))
    assert finding["misplaced"] == 1
    assert finding["out_of_place"][0] in {"#/b", "#/c"}


def test_a_full_width_block_separates_what_it_sits_between():
    # The heading spans both columns, so the column-major run restarts under it:
    # a, b (left), then c, d (right), then the heading, then e (left), f (right).
    page = [
        block("#/a", LEFT, 700.0), block("#/b", LEFT, 650.0),
        block("#/c", RIGHT, 700.0), block("#/d", RIGHT, 650.0),
        block("#/head", LEFT, 600.0, width=504.0, kind=BlockType.HEADING),
        block("#/e", LEFT, 550.0), block("#/f", RIGHT, 550.0),
    ]
    order = emitted("#/a", "#/b", "#/c", "#/d", "#/head", "#/e", "#/f")
    assert page_findings(page, order) is None


def test_a_block_emitted_far_from_where_it_is_printed_is_one_move():
    # A block at the top of the right column emitted last: the page's other six
    # are in order, so the smallest correction is to move this one.
    page = [*two_column_page(), block("#/hdr", RIGHT, 760.0, width=60.0)]
    order = emitted("#/a", "#/b", "#/c", "#/d", "#/e", "#/f", "#/hdr")
    finding = page_findings(page, order)
    assert finding["misplaced"] == 1
    assert finding["out_of_place"] == ["#/hdr"]


def test_floats_and_their_captions_are_not_held_to_the_flow():
    # A figure sits where the layout puts it; emitting it elsewhere is not a
    # reading-order defect, so it must not create one.
    page = [
        *two_column_page(),
        block("#/fig", LEFT, 760.0, kind=BlockType.FIGURE),
        block("#/cap", LEFT, 740.0, kind=BlockType.CAPTION),
    ]
    order = emitted("#/a", "#/b", "#/c", "#/d", "#/e", "#/f", "#/fig", "#/cap")
    assert page_findings(page, order) is None


def test_blocks_that_start_at_no_column_are_set_aside_not_forced():
    # A masthead date and a shattered fragment begin nowhere a column begins.
    # Forcing them into the nearest column is what turns a correct page into a
    # list of inversions.
    page = [*two_column_page(),
            block("#/date", 368.0, 90.0, width=40.0),
            block("#/frag", 152.0, 300.0, width=20.0)]
    order = emitted("#/a", "#/b", "#/c", "#/d", "#/e", "#/f", "#/date", "#/frag")
    assert page_findings(page, order) is None


def test_a_page_the_column_model_cannot_describe_is_refused():
    page = [block(f"#/x{i}", 40.0 + i * 37.0, 700.0 - i * 30.0, width=30.0)
            for i in range(8)]
    assert page_findings(page, emitted(*(f"#/x{i}" for i in range(8)))) is None


def test_a_short_page_is_not_measured():
    page = [block("#/a", LEFT, 700.0), block("#/b", LEFT, 600.0)]
    assert page_findings(page, emitted("#/b", "#/a")) is None


def test_flags_name_the_page_and_carry_a_marker():
    page = two_column_page()
    flags, pages = reading_order_flags(
        page, spans("#/a", "#/d", "#/b", "#/e", "#/c", "#/f")
    )
    assert [f.page for f in flags] == [1]
    assert "2-column page" in flags[0].reason
    assert "2 of 6 blocks" in flags[0].reason
    assert "source page 1" in flags[0].marker_text
    assert flags[0].disposition == "action_required"
    assert pages["1"]["geometry"]["misplaced"] == 2


def test_blocks_the_emitter_never_placed_are_skipped():
    page = two_column_page()
    # Only three carry an emission span; the rest can't be compared at all.
    assert page_findings(page, emitted("#/a", "#/b", "#/c")) is None


def numbered(prefix: str, count: int, start: int = 1) -> list[Block]:
    """A reference list: one block per entry, each opening with its ordinal."""
    entries = []
    for i, n in enumerate(range(start, start + count)):
        entry = block(f"#/r{n}", LEFT, 700.0 - i * 20.0, kind=BlockType.LIST)
        entry.text = prefix.format(n=n) + " Author, A. Journal 1, 1 (2020)."
        entries.append(entry)
    return entries


def test_ordinals_in_sequence_are_not_reported():
    entries = numbered("({n})", 8)
    assert ordinal_findings(entries, emitted(*(b.id for b in entries))) is None


def test_swapped_numbered_entries_are_proof_not_inference():
    entries = numbered("({n})", 8)
    ids = [b.id for b in entries]
    ids[2], ids[3] = ids[3], ids[2]
    finding = ordinal_findings(entries, emitted(*ids))
    assert finding["misplaced"] == 1
    assert finding["range"] == [1, 8]
    assert finding["entries"] == 8


def test_every_ordinal_form_a_reference_list_uses_is_read():
    for prefix in ("{n}", "({n})", "[{n}]", "{n}.", "<sup>{n}</sup>"):
        entries = numbered(prefix, 6)
        ids = [b.id for b in entries]
        ids[0], ids[1] = ids[1], ids[0]
        assert ordinal_findings(entries, emitted(*ids)) is not None, prefix


def test_numbers_that_are_not_a_list_prove_nothing():
    # Leading numbers with gaps could be anything — years, quantities, section
    # numbers of a subset. Only an unbroken run is the printed order.
    entries = numbered("({n})", 6)
    entries[3].text = "(99) Author, A. Journal 1, 1 (2020)."
    ids = [b.id for b in entries]
    ids[0], ids[1] = ids[1], ids[0]
    assert ordinal_findings(entries, emitted(*ids)) is None


def test_a_year_is_not_an_ordinal():
    entries = numbered("({n})", 6)
    entries[0].text = "2016 was the year the framework was published."
    assert ordinal_findings(entries, emitted(*(b.id for b in entries))) is None


def test_numbering_convicts_a_page_the_geometry_cannot_resolve():
    # All one column, so geometry has nothing to say; the numbering still does,
    # and the flag it raises is high rather than medium.
    entries = numbered("({n})", 8)
    ids = [b.id for b in entries]
    ids[2], ids[5] = ids[5], ids[2]
    flags, pages = reading_order_flags(entries, spans(*ids))
    assert [f.severity for f in flags] == ["high"]
    assert "numbered entries (1-8)" in flags[0].reason
    assert "numbering" in pages["1"]


def _flow(bid: str, top: float, text: str) -> Block:
    """A single-column block carrying its own text, for the fragment tests."""
    return Block(id=bid, type=BlockType.PARAGRAPH, text=text, page=1,
                 bbox=BBox(x0=LEFT, y0=top - 20.0, x1=LEFT + WIDTH, y1=top))


def test_a_shattered_equation_is_not_read_as_disorder():
    # Docling breaks a display equation into per-glyph `paragraph` blocks and
    # emits them after the prose they sit above. That is out of geometric order
    # and tells a reader nothing: the "misplaced" content is the character `d`.
    # The prose is in order, so the page has no finding.
    blocks = [
        _flow("#/p1", 700, "The reaction Gibbs energy is defined as follows"),
        _flow("#/p2", 650, "This equation can be reorganized into the form"),
        _flow("#/p3", 500, "That is, the slope of the curve at that point"),
        _flow("#/f1", 600, "d"),
        _flow("#/f2", 590, "G"),
        _flow("#/f3", 580, "=m"),
    ]
    assert page_findings(blocks, emitted(*[b.id for b in blocks])) is None


def test_a_one_word_heading_still_counts_as_flow():
    # The bound is characters, not words, so a real one-word heading is kept and
    # emitting it out of place is still reported.
    blocks = [
        _flow("#/h", 700, "ACKNOWLEDGMENTS"),
        _flow("#/p1", 650, "We thank the reviewers for their careful reading"),
        _flow("#/p2", 600, "This work was supported by a grant from the council"),
        _flow("#/p3", 550, "The authors declare no competing financial interest"),
    ]
    finding = page_findings(blocks, emitted("#/p1", "#/p2", "#/p3", "#/h"))
    assert finding is not None and finding["misplaced"] >= 1


def test_split_lines_ignores_shattered_fragments_too():
    # The order check and the split-line check must agree on what a block is.
    # They did not: only the first excluded fragments, so a display equation
    # broken into per-glyph blocks read as printed lines cut into pieces.
    from pdf2md.reading_order import split_line_findings

    blocks = [
        _flow("#/p1", 700, "The reaction Gibbs energy is defined as follows"),
        _flow("#/p2", 650, "This equation can be reorganized into the form"),
        _flow("#/p3", 600, "That is, the slope of the curve at that point"),
        _flow("#/f1", 640, "d"),
        _flow("#/f2", 641, "G"),
        _flow("#/f3", 642, "=m"),
    ]
    assert split_line_findings(blocks, emitted(*[b.id for b in blocks])) is None


def test_split_lines_needs_every_piece_to_be_one_printed_line():
    from pdf2md.reading_order import split_line_findings

    # Two consecutive paragraphs in a single column, overlapping by the two
    # points between one's descenders and the next's ascenders. Band overlap
    # calls them one printed line; counting their printed lines does not.
    paragraphs = [
        block("#/p1", LEFT, 400.0), block("#/p2", LEFT, 382.0),
        block("#/p3", LEFT, 300.0), block("#/p4", LEFT, 200.0),
    ]
    at = emitted(*[b.id for b in paragraphs])
    assert split_line_findings(paragraphs, at, lambda b: 1)["lines"] == 1
    assert split_line_findings(paragraphs, at, lambda b: 9 if b.id == "#/p1" else 1) is None

    # No counter at all (a scanned page has no layer to count) claims nothing,
    # rather than falling back to the band overlap that was never evidence.
    assert split_line_findings(paragraphs, at) is None
    assert split_line_findings(paragraphs, at, lambda b: 0) is None
