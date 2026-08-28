"""Detect preformatted monospace content — console/terminal transcripts and
ASCII-art tables — that the prose/table model destroys.

Software manuals (GRASP) embed program I/O sessions and ASCII tables: monospace
text whose meaning is in its line structure (banner lines, column rules, prompts).
Docling flattens such a block into one prose paragraph or mis-grids the table, and
the reading-order collapse loses the layout. A block detected here is re-read with
pdfium's native line breaks and emitted as a fenced code block instead.

The signals are decoration lines (a line almost entirely `*`/`-`/`=`/`_`/`#` rule
or banner characters) and literal pipe columns (`a | b | c` rows), neither of which
appears in a real PDF table's text layer or in normal prose — so clean papers and
genuine grids are untouched. Used on blocks the engine already calls tables.
"""

from __future__ import annotations

_DECO = set("*-=_#~")


def _is_decoration(line: str) -> bool:
    s = line.strip()
    return len(s) >= 4 and sum(c in _DECO for c in s) / len(s) >= 0.8


def is_preformatted(text: str, *, pipes: bool = False) -> bool:
    """True when `text` (read with line breaks preserved) is a console listing or
    ASCII-art table. Banner/rule lines are the precise signal used everywhere (prose
    never has two). `pipes=True` also accepts literal `|` column rows — enabled only
    for blocks the engine already calls tables, where a pipe is a column rule, not
    the bra-ket/abs-value a prose paragraph might contain."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 3:
        return False
    if sum(1 for ln in lines if _is_decoration(ln)) >= 2:
        return True
    return pipes and sum(1 for ln in lines if ln.count("|") >= 2) >= 2
