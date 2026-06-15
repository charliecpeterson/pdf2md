"""Render a table block: GFM by default, HTML only when cells merge/span (which
GFM can't express). The engine's markdown export prefixes the caption, which is
already a separate caption block, so we strip everything before the first row."""

from __future__ import annotations

from pdf2md.schema import TableData


def render_table(table: TableData) -> str:
    if table.has_spanning_cells and table.html:
        return table.html
    return _strip_caption(table.gfm)


def _strip_caption(gfm: str) -> str:
    lines = gfm.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.lstrip().startswith("|")), 0)
    return "\n".join(lines[start:]).strip()
