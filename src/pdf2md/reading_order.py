"""Check that the emitted order of a page's prose matches the order it is printed in.

The rest of the verification layer is deliberately order-insensitive: word recall
compares multisets so a scrambled draw order doesn't read as loss, and numeric
conservation counts values, not positions. That makes one defect invisible to all
of it — a two-column page whose columns the engine interleaves conserves every
word and number and reads as nonsense. `quality.py` says so in the scorecard:
"Block accounting does not measure reading order or region-boundary accuracy."

Two independent mechanisms answer it. `page_findings` reads the page's geometry:
columns are where blocks' left edges cluster, a block running into the next
column separates what comes before it from what comes after, and within a segment
the printed order is column-major. `ordinal_findings` reads the document's own
numbering instead — when a page's leading ordinals sort to an unbroken run, that
run *is* the printed order, with no column model and nothing to tune.

They fail for different reasons: geometry is blind to a page it can't resolve
into columns, numbering to a page that isn't numbered. Each covers the other's
gap, and a page both convict is beyond argument.

Conservative like the rest. Geometry refuses when the columns don't resolve or
don't account for the blocks, numbering refuses unless the ordinals are provably
a list, and both look only at prose: figures, tables, captions, and footnotes sit
where the layout puts them, and holding them to the flow would report the page's
design as a defect. What is reported is the minimum number of blocks whose
removal restores the order, never the count of inverted pairs.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any

from pdf2md.schema import BBox, Block, BlockType, CoverageFlag

# Types whose position carries the reading flow. A float (figure, table), its
# caption, or a footnote is placed by the layout, not the argument.
_FLOW_TYPES = frozenset({BlockType.PARAGRAPH, BlockType.HEADING, BlockType.LIST})
# How far two blocks' left edges may differ and still be the same column: a
# paragraph indent, a bullet's hanging text, a slightly overhanging box.
_COLUMN_TOLERANCE_PT = 12.0
# A column start needs at least this many blocks, and this share of the page's,
# behind it to be a column.
_MIN_COLUMN_BLOCKS = 2
_MIN_COLUMN_SHARE = 0.15
# Past this the layout hasn't resolved into columns and there is nothing
# trustworthy to say about the order.
_MAX_COLUMNS = 4
# And past this share of blocks the columns don't account for, likewise.
_MAX_UNPLACED_SHARE = 0.35
# A numbered entry opens with its ordinal: `(44)`, `[44]`, `44.`, a bare `44`,
# or the superscript the emitter recovered from glyph geometry.
_ORDINAL = re.compile(
    r"^\s*(?:<sup>\s*)?[\[(]?\s*(\d{1,3})\s*(?:[.)\]]|</sup>)?\s*(?:</sup>)?\s+\S"
)
# Fewer than this many numbered entries on a page is a coincidence, not a list.
_MIN_ORDINALS = 5
# Below this many flow blocks a page has no order worth checking.
_MIN_BLOCKS = 4
# A block of one or two characters is not content with a place in the flow: it
# is a piece of something the engine shattered. Docling breaks a display
# equation into per-glyph `paragraph` blocks -- one Atkins page yields `A`, `∂`,
# `G`, `∂ξ`, `=µ`, `p`, `,` as fourteen separate ones -- and emits them after
# the prose they sit above, which reads as disorder while telling a reader
# nothing they can act on. Of 148 flagged pages, 22 had no out-of-order block
# longer than two words. Excluding them clears 20 findings and reveals 3 more,
# where the fragments had been padding the ordered run and hiding real prose
# disorder. A one-word heading (`ACKNOWLEDGMENTS`, `References`) is well clear
# of this, which is why the bound is characters and not words.
_MIN_FLOW_CHARS = 3


def _span(bbox: BBox) -> tuple[float, float]:
    return min(bbox.x0, bbox.x1), max(bbox.x0, bbox.x1)


def _top(bbox: BBox) -> float:
    return max(bbox.y0, bbox.y1)


def column_starts(boxes: list[BBox]) -> list[float]:
    """The x each column begins at, left to right.

    Left edges, not whitespace corridors. A column layout is defined by where its
    text starts, and that survives what defeats a corridor search: an abstract or
    a wide figure caption that overhangs the gutter leaves no clear vertical
    channel, but every body block in the left column still begins at the same x.
    A start needs two blocks to be a column -- one block sharing an edge is an
    indent or a stray."""
    lefts = sorted(_span(b)[0] for b in boxes)
    if not lefts:
        return []
    clusters: list[list[float]] = [[lefts[0]]]
    for left in lefts[1:]:
        if left - clusters[-1][0] <= _COLUMN_TOLERANCE_PT:
            clusters[-1].append(left)
        else:
            clusters.append([left])
    # A share of the page's blocks, not a fixed count: a reference list shattered
    # into fragments repeats stray left edges often enough to clear any small
    # constant, and each one would invent a column.
    floor = max(_MIN_COLUMN_BLOCKS, math.ceil(_MIN_COLUMN_SHARE * len(lefts)))
    starts = [c[0] for c in clusters if len(c) >= floor]
    return starts or [lefts[0]]


def _starts_a_column(bbox: BBox, starts: list[float]) -> bool:
    """Whether the block begins where some column begins. One that doesn't is
    outside the layout this model describes -- a date field in a masthead, a
    fragment of a shattered reference -- and nothing here can say where it
    belongs in the printed order."""
    left = _span(bbox)[0]
    return any(abs(left - start) <= _COLUMN_TOLERANCE_PT for start in starts)


def _column_of(bbox: BBox, starts: list[float]) -> int | None:
    """The column the block sits in, or None when it runs into the next one --
    a full-width title, a spanning heading, a table stretched across the page."""
    left, right = _span(bbox)
    index = next(
        (i for i, start in enumerate(starts)
         if abs(left - start) <= _COLUMN_TOLERANCE_PT),
        None,
    )
    if index is None:
        return None
    if index + 1 < len(starts) and right > starts[index + 1] + _COLUMN_TOLERANCE_PT:
        return None
    return index


def _printed_order(blocks: list[Block], starts: list[float]) -> list[Block]:
    """The page's blocks in the order the page prints them: column-major within
    each run of blocks between full-width elements, which act as separators."""
    ordered: list[Block] = []
    segment: list[Block] = []

    def flush() -> None:
        by_column: dict[int, list[Block]] = defaultdict(list)
        for block in segment:
            by_column[_column_of(block.bbox, starts)].append(block)
        for column in sorted(by_column):
            ordered.extend(sorted(by_column[column], key=lambda b: -_top(b.bbox)))
        segment.clear()

    for block in sorted(blocks, key=lambda b: -_top(b.bbox)):
        if _column_of(block.bbox, starts) is None:
            flush()
            ordered.append(block)
        else:
            segment.append(block)
    flush()
    return ordered


def page_findings(blocks: list[Block], emitted_at: dict[str, int]) -> dict[str, Any] | None:
    """How far one page's emitted order departs from its printed order, or None
    when there is nothing measurable: too few blocks in the flow, a layout that
    doesn't resolve into columns, or an order that already matches."""
    flow = [
        b for b in blocks
        if b.type in _FLOW_TYPES and b.bbox is not None and b.id in emitted_at
        and len(b.text.strip()) >= _MIN_FLOW_CHARS
    ]
    if len(flow) < _MIN_BLOCKS:
        return None
    starts = column_starts([b.bbox for b in flow])
    if len(starts) > _MAX_COLUMNS:
        return None
    # Blocks the column model can't place are set aside rather than guessed at.
    # Setting them aside is what keeps the check honest on a real page: a
    # masthead date, a running header the engine typed as prose, a fragment of a
    # shattered reference all begin nowhere a column begins, and forcing each
    # into the nearest column is what turns a correctly ordered page into a
    # list of inversions. Past a large share of the page the model isn't
    # describing this layout at all, and its verdict would be noise.
    placed = [b for b in flow if _starts_a_column(b.bbox, starts)]
    if len(flow) - len(placed) > _MAX_UNPLACED_SHARE * len(flow):
        return None
    flow = placed
    if len(flow) < _MIN_BLOCKS:
        return None
    printed = _printed_order(flow, starts)
    rank = {block.id: i for i, block in enumerate(printed)}

    emitted = sorted(flow, key=lambda b: emitted_at[b.id])
    sequence = [rank[b.id] for b in emitted]
    keep = _longest_increasing(sequence)
    misplaced = [
        emitted[i].id for i in range(len(sequence)) if i not in keep
    ]
    if not misplaced:
        return None
    return {
        "columns": len(starts),
        "blocks": len(flow),
        "misplaced": len(misplaced),
        "out_of_place": sorted(misplaced, key=lambda bid: rank[bid]),
        "expected": [block.id for block in printed],
    }


def _longest_increasing(sequence: list[int]) -> set[int]:
    """Positions of a longest run that is already in printed order.

    What's left is the smallest set of blocks whose removal would leave the page
    ordered, which is the honest count of how many are misplaced. Counting
    inverted *pairs* instead reports one block dragged to the end of a page as
    every block it jumped over."""
    best: list[int] = []           # best[k] = index ending the best run of length k+1
    previous: dict[int, int] = {}
    for i, value in enumerate(sequence):
        lo, hi = 0, len(best)
        while lo < hi:
            mid = (lo + hi) // 2
            if sequence[best[mid]] < value:
                lo = mid + 1
            else:
                hi = mid
        previous[i] = best[lo - 1] if lo else -1
        if lo == len(best):
            best.append(i)
        else:
            best[lo] = i
    kept: set[int] = set()
    node = best[-1] if best else -1
    while node >= 0:
        kept.add(node)
        node = previous[node]
    return kept


def _leading_ordinal(text: str) -> int | None:
    match = _ORDINAL.match(text)
    return int(match.group(1)) if match else None


def ordinal_findings(blocks: list[Block], emitted_at: dict[str, int]) -> dict[str, Any] | None:
    """Order checked against the document's own numbering, with no geometry.

    A reference list, a numbered equation set, an enumerated procedure: each
    entry opens with its ordinal, and the page prints them in order. When the
    ordinals on a page sort to a contiguous run, that run *is* the printed order
    -- there is no threshold, no column model, and nothing to tune. It covers
    fewer pages than the geometric check and answers with certainty on the ones
    it covers, which is what makes the two worth having together."""
    numbered = [
        (block, ordinal) for block in sorted(blocks, key=lambda b: emitted_at.get(b.id, -1))
        if block.id in emitted_at and (ordinal := _leading_ordinal(block.text)) is not None
    ]
    if len(numbered) < _MIN_ORDINALS:
        return None
    ordinals = [ordinal for _, ordinal in numbered]
    # Exactly one of each, covering an unbroken range: anything less and the
    # numbers might not be a list at all, and their order proves nothing.
    if sorted(ordinals) != list(range(min(ordinals), min(ordinals) + len(ordinals))):
        return None
    keep = _longest_increasing(ordinals)
    misplaced = [
        numbered[i][0].id for i in range(len(ordinals)) if i not in keep
    ]
    if not misplaced:
        return None
    return {
        "entries": len(numbered),
        "range": [min(ordinals), max(ordinals)],
        "misplaced": len(misplaced),
        "out_of_place": misplaced,
        "emitted": ordinals,
    }


def split_line_findings(
    blocks: list[Block], emitted_at: dict[str, int]
) -> dict[str, Any] | None:
    """Printed lines the engine split across several blocks.

    Within one column, one printed line belongs to one block. Two blocks whose
    vertical bands overlap inside the same column means a line was cut into
    pieces -- `'Serre,'`, `'C.;'`, `'rey, G.'` are three blocks of one reference
    entry, and `'rey, G.'` doesn't even start at a word boundary.

    Reported informational, never as an action. The detection is exact but the
    judgement isn't: a masthead's `Received:` / `July 5, 2016` is also one
    printed line in two blocks, and there it is the layout, not a defect. Saying
    what was measured and leaving the verdict to a reader is the honest form."""
    flow = [
        b for b in blocks
        if b.type in _FLOW_TYPES and b.bbox is not None and b.id in emitted_at
    ]
    if len(flow) < _MIN_BLOCKS:
        return None
    starts = column_starts([b.bbox for b in flow])
    if len(starts) > _MAX_COLUMNS:
        return None

    lines: list[list[Block]] = []
    for block in sorted(flow, key=lambda b: (-_top(b.bbox), _span(b.bbox)[0])):
        column = _column_of(block.bbox, starts)
        if column is None:  # a full-width element shares no line with anything
            continue
        low, high = min(block.bbox.y0, block.bbox.y1), _top(block.bbox)
        for line in lines:
            first = line[0]
            if (_column_of(first.bbox, starts) == column
                    and low < _top(first.bbox)
                    and high > min(first.bbox.y0, first.bbox.y1)):
                line.append(block)
                break
        else:
            lines.append([block])

    split = [line for line in lines if len(line) > 1]
    if not split:
        return None
    return {
        "lines": len(split),
        "blocks": sum(len(line) for line in split),
        "samples": [
            " | ".join(b.text[:24] for b in line) for line in split[:4]
        ],
    }


def reading_order_flags(
    blocks: list[Block], emission_index: dict[str, dict[str, Any]]
) -> tuple[list[CoverageFlag], dict[str, Any]]:
    """One action per page whose emitted order departs from its printed order,
    plus the per-page detail for `profile.json`.

    Two independent mechanisms answer the same question: the page's geometry and
    the document's own numbering. They fail for different reasons -- geometry is
    blind to a page it can't resolve into columns, numbering is blind to a page
    that isn't numbered -- so each covers the other's gap, and a page both
    convict is beyond argument."""
    by_page: dict[int, list[Block]] = defaultdict(list)
    for block in blocks:
        by_page[block.page].append(block)
    # Both keys, because a flag is only placeable where the annotation pass can
    # find a span *and* a file to put it in.
    emitted_at = {
        block_id: int(entry["start"])
        for block_id, entry in emission_index.items()
        if entry.get("start") is not None and entry.get("markdown")
    }

    flags: list[CoverageFlag] = []
    notes: list[CoverageFlag] = []
    pages: dict[str, Any] = {}
    for page in sorted(by_page):
        geometry = page_findings(by_page[page], emitted_at)
        numbering = ordinal_findings(by_page[page], emitted_at)
        split = split_line_findings(by_page[page], emitted_at)
        if split is not None:
            notes.append(CoverageFlag(
                by_page[page][0].id, page,
                f"split lines: {split['lines']} printed line(s) on this page are cut "
                f"across {split['blocks']} blocks",
                "", disposition="informational", severity="low", content_impact="low",
            ))
        if geometry is None and numbering is None:
            if split is not None:
                pages[str(page)] = {"split_lines": split}
            continue
        detail: dict[str, Any] = {}
        if geometry is not None:
            detail["geometry"] = geometry
        if numbering is not None:
            detail["numbering"] = numbering
        if split is not None:
            detail["split_lines"] = split
        pages[str(page)] = detail

        # The numbering is proof, not evidence: an unbroken run of ordinals is
        # the printed order with nothing inferred. A page it convicts is high.
        severity = "high" if numbering is not None else "medium"
        reason = f"reading order: {_order_reason(geometry, numbering)}"
        anchor = (numbering or geometry)["out_of_place"][0]
        flags.append(CoverageFlag(
            anchor, page, reason,
            f"> **[pdf2md: action required ({severity}): {reason}; verify against "
            f"[source page {page}](../source.pdf#page={page})]**",
            disposition="action_required", severity=severity, content_impact=severity,
        ))
    return flags + notes, pages


def _order_reason(geometry: dict | None, numbering: dict | None) -> str:
    if numbering is not None:
        low, high = numbering["range"]
        part = (
            f"{numbering['misplaced']} of {numbering['entries']} numbered entries "
            f"({low}-{high}) are emitted out of sequence"
        )
        if geometry is not None:
            part += f", and the page's own layout puts {geometry['misplaced']} out of place"
        return part
    layout = (
        f"{geometry['columns']}-column" if geometry["columns"] > 1 else "single-column"
    )
    part = (
        f"{geometry['misplaced']} of {geometry['blocks']} blocks on this {layout} page "
        f"are emitted out of printed order"
    )
    return part
