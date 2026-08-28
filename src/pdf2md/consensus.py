"""Agreement scoring for repeated model reads of one crop.

Two strategies for the two shapes of re-read content. The scanned-prose OCR pass re-reads
one paragraph, where the right answer is a single coherent transcription — `consensus_pick`
keeps the modal reading and flags when the numbers disagree. Figure labels are instead a
*set* of independent items (axis titles, peak values, ticks), where a vote that reads a tick
another missed adds real content, not noise — `merge_reads` unions them and flags only a
genuine slip. Both measure agreement on the numeric tokens, not on prose wording a vision
model freely paraphrases: `0.38` vs `0.88` on the same item is exactly what to flag, while a
differently-worded axis title carrying the same value is not.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter

_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?")
_SAME_LINE = 0.9  # a label this textually close to one already kept is a re-read of it


def _line_key(line: str) -> str:
    """Compare labels ignoring case, spacing, and math delimiters, so a vision model's
    `$P(r)$`, `$$P(r)$$`, and `P(r)` for one axis title count as the same line, not three."""
    return re.sub(r"\s+", " ", line.replace("$", "")).strip().lower()


def numeric_tokens(text: str) -> list[str]:
    """The numbers in `text`, in order, with thousands separators dropped."""
    return [t.replace(",", "") for t in _NUMBER.findall(text)]


def consensus_pick(reads: list[str]) -> tuple[str, bool]:
    """From repeated reads of one crop, return (chosen text, numbers_agreed). Keeps the
    reading whose numeric sequence is the most common; numbers_agreed is False when the
    reads disagree on their numbers, so the caller can flag the block."""
    reads = [r for r in reads if r]
    if len(reads) <= 1:
        return (reads[0] if reads else ""), True
    sigs = [tuple(numeric_tokens(r)) for r in reads]
    top_sig, _ = Counter(sigs).most_common(1)[0]
    chosen = next(r for r, s in zip(reads, sigs) if s == top_sig)
    return chosen, len(set(sigs)) == 1


def merge_reads(reads: list[str]) -> tuple[str, bool]:
    """From repeated reads of one figure crop, return (unioned text, has_conflict). Labels
    are a set, so a value only one vote saw is kept, not out-voted; lines are unioned in
    first-seen order. A line textually close to one already kept is the same label re-read:
    dropped if its numbers match (an exact repeat), else kept alongside it and flagged as a
    conflict — a digit slip on the same label, which a human should check. A line no earlier
    vote saw has no twin and simply joins the union, so reading *more* never trips the flag."""
    reads = [r for r in reads if r]
    if len(reads) <= 1:
        return (reads[0] if reads else ""), False
    kept: list[str] = []
    conflict = False
    for r in reads:
        for ln in r.splitlines():
            if not any(c.isalnum() for c in ln):
                continue  # blank or pure-punctuation (a stray "---" rule) carries no label
            # normalized (case, spacing, math delimiters) so a re-read of one label matches
            key = _line_key(ln)
            twin = next((k for k in kept if difflib.SequenceMatcher(
                None, key, _line_key(k)).ratio() >= _SAME_LINE), None)
            if twin is not None and numeric_tokens(ln) == numeric_tokens(twin):
                continue  # an exact re-read of a label already kept
            if twin is not None:  # same label, different numbers: keep both, flag the slip
                conflict = True
            kept.append(ln)
    return "\n".join(kept), conflict


def table_cell_consensus(reads: dict[str, list[list[str]]]) -> list[dict[str, object]]:
    """Compare aligned table grids cell by cell without manufacturing a correction.

    A unanimous value is safe to expose as the consensus. A strict majority is recorded
    as a candidate while preserving every reader's value; ties stay unresolved. Missing
    cells do not vote, but their absence is explicit in the readings map.
    """
    if not reads:
        return []
    row_count = max((len(grid) for grid in reads.values()), default=0)
    cells = []
    for row in range(row_count):
        column_count = max(
            (len(grid[row]) for grid in reads.values() if row < len(grid)),
            default=0,
        )
        for column in range(column_count):
            readings = {
                reader: grid[row][column] if row < len(grid) and column < len(grid[row]) else None
                for reader, grid in reads.items()
            }
            present = [value for value in readings.values() if value is not None]
            counts = Counter(present)
            if not present:
                status, selected = "missing", None
            elif len(counts) == 1 and len(present) == len(reads):
                status, selected = "agree", present[0]
            elif len(present) == 1:
                status, selected = "single_read", present[0]
            else:
                selected, votes = counts.most_common(1)[0]
                if votes > len(present) / 2:
                    status = "majority"
                else:
                    status, selected = "disagree", None
            cells.append({
                "row": row,
                "column": column,
                "status": status,
                "selected": selected,
                "readings": readings,
            })
    return cells
