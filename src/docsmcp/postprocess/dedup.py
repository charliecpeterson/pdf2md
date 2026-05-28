from __future__ import annotations

import re

_PIPE_ROW = re.compile(r"^\s*\|.*\|\s*$")

_MIN_LINE_RUN = 4
_MIN_CELL_RUN = 4


def _collapse_line_runs(text: str) -> str:
    """Collapse a vertical run of 4+ identical non-blank lines into one with '(×N)' suffix."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        j = i
        while j + 1 < n and lines[j + 1] == lines[i] and lines[i].strip():
            j += 1
        run = j - i + 1
        if run >= _MIN_LINE_RUN:
            stripped = lines[i].rstrip()
            out.append(f"{stripped}  (×{run})")
        else:
            out.extend(lines[i : j + 1])
        i = j + 1
    return "\n".join(out)


def _collapse_cell_runs_in_row(row: str) -> str:
    """Within a single | a | b | b | b | row, collapse 4+ identical adjacent cells."""
    if not _PIPE_ROW.match(row):
        return row
    parts = [c.strip() for c in row.strip().strip("|").split("|")]
    out_parts: list[str] = []
    i = 0
    n = len(parts)
    while i < n:
        j = i
        while j + 1 < n and parts[j + 1] == parts[i]:
            j += 1
        run = j - i + 1
        if run >= _MIN_CELL_RUN and parts[i]:
            out_parts.append(f"{parts[i]} (×{run})")
        else:
            out_parts.extend(parts[i : j + 1])
        i = j + 1
    return "| " + " | ".join(out_parts) + " |"


def _collapse_cell_runs(text: str) -> str:
    return "\n".join(_collapse_cell_runs_in_row(line) for line in text.split("\n"))


def dedup_repeats(text: str) -> str:
    """Collapse run-length repeats — both vertical line runs and within-row cell runs.

    Conservative: only fires on 4+ identical neighbors. Preserves non-repeating content
    untouched. Designed to clean up tables and figure-axis dumps in chunk text.
    """
    if not text:
        return text
    text = _collapse_cell_runs(text)
    text = _collapse_line_runs(text)
    return text
