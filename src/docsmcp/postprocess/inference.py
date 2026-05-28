"""Shared number-inference for equations, tables, and figures.

All three caption-bearing entities share the same problem: some items have an
extracted number (anchor), most don't. We want to fill in the missing numbers
by interpolating between anchors and extrapolating off the edges, conservatively.

Three failure modes guarded against:
  1. Out-of-order anchors (false positives) — dropped silently.
  2. Duplicate numbers across items (e.g., two equations both inferred as "17").
  3. Runaway extrapolation past the last real anchor (e.g., "67" → "310" 5 items later).

The protocol is duck-typed: items need .number (str|None), .inferred (bool),
.flags (list[str]). The optional anchor_parser callable lets callers loosen what
counts as an integer anchor (e.g., tables accept "1.1" → 1).
"""

from __future__ import annotations

from typing import Any, Callable

_DEFAULT_MAX_EDGE_EXTRAPOLATION = 5
_DEFAULT_INTERIOR_TOLERANCE = 2
_DEFAULT_INTERIOR_MAX_GAP = 12


def _default_anchor_parser(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _table_anchor_parser(s: str | None) -> int | None:
    """Tables sometimes have decimal-form labels like '1.1' or 'A.3' — extract
    the leading integer when present."""
    if not s:
        return None
    head = s.split(".")[0]
    try:
        return int(head)
    except ValueError:
        return None


def infer_numbers(
    items: list[Any],
    *,
    anchor_parser: Callable[[str | None], int | None] = _default_anchor_parser,
    max_edge_extrapolation: int = _DEFAULT_MAX_EDGE_EXTRAPOLATION,
    interior_tolerance: int = _DEFAULT_INTERIOR_TOLERANCE,
    interior_max_gap: int = _DEFAULT_INTERIOR_MAX_GAP,
) -> None:
    """Fill in missing `number` fields on items by inferring from anchors.

    Mutates items in place. Items must expose:
      - `.number: str | None`  — read/write
      - `.inferred: bool`      — write (True for filled-in numbers)
      - `.flags: list[str]`    — write (adds "number_dropped_out_of_order" / "inference_skipped_dup")

    Strategy (in order):
      1. Collect anchors (items with a parseable integer number).
      2. Drop anchors that break monotonic increase — they're false positives.
      3. Interior interpolation: for missing items between two anchors,
         fill in if the seq gap roughly matches the number gap.
      4. Leading-edge extrapolation: max `max_edge_extrapolation` items before first anchor.
      5. Trailing-edge extrapolation: same cap past last anchor.
    """
    if not items:
        return

    raw_anchors = [
        (i, anchor_parser(it.number))
        for i, it in enumerate(items)
        if anchor_parser(it.number) is not None
    ]
    if not raw_anchors:
        return

    # Drop anchors that break monotonic order — typically false-positive extractions
    # (e.g., a stray number that's smaller than its predecessor).
    anchors: list[tuple[int, int]] = []
    for j, n in raw_anchors:
        if not anchors or n > anchors[-1][1]:
            anchors.append((j, n))
        else:
            items[j].number = None
            items[j].flags.append("number_dropped_out_of_order")
    if not anchors:
        return

    claimed: set[int] = {n for _, n in anchors}

    # Interior interpolation
    if len(anchors) >= 2:
        for i, item in enumerate(items):
            if item.number:
                continue
            before = [(j, n) for j, n in anchors if j < i]
            after = [(j, n) for j, n in anchors if j > i]
            if not (before and after):
                continue
            j_before, n_before = before[-1]
            j_after, n_after = after[0]
            gap_seq = j_after - j_before
            gap_num = n_after - n_before
            if gap_num <= 0:
                continue
            if gap_num == gap_seq or (
                abs(gap_seq - gap_num) <= interior_tolerance
                and gap_seq <= interior_max_gap
            ):
                inferred = n_before + (i - j_before)
                if inferred >= n_after:
                    inferred = n_after - 1
                if inferred > n_before and inferred not in claimed:
                    item.number = str(inferred)
                    item.inferred = True
                    claimed.add(inferred)
                else:
                    item.flags.append("inference_skipped_dup")

    # Leading-edge extrapolation — capped to avoid runaway when the first anchor
    # is far past the start (e.g., textbook where eq 1 isn't found until block 200).
    first_idx, first_num = anchors[0]
    if first_num > first_idx and first_idx <= max_edge_extrapolation:
        for i in range(first_idx):
            if items[i].number:
                continue
            cand = first_num - (first_idx - i)
            if cand > 0 and cand not in claimed:
                items[i].number = str(cand)
                items[i].inferred = True
                claimed.add(cand)

    # Trailing-edge extrapolation — same cap. Without this, a single extracted
    # number (e.g., a stray "(67)") propagates to every subsequent item.
    last_idx, last_num = anchors[-1]
    for i in range(last_idx + 1, len(items)):
        gap = i - last_idx
        if gap > max_edge_extrapolation:
            break
        if items[i].number:
            continue
        cand = last_num + gap
        if cand not in claimed:
            items[i].number = str(cand)
            items[i].inferred = True
            claimed.add(cand)


# Convenience wrapper for tables (lenient anchor parsing).
def infer_numbers_table(items: list[Any], **kwargs: Any) -> None:
    kwargs.setdefault("anchor_parser", _table_anchor_parser)
    return infer_numbers(items, **kwargs)
