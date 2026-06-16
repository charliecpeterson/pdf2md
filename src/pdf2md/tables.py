"""Table markup. `render_table` picks the right serialization of an already-built
table; `build_html`/`build_gfm` assemble markup from a neutral cell grid (used when
the engine adapter rebuilds a table to inject sub/superscripts). Cell text is final
by the time it reaches the builders — the adapter handles escaping and scripts."""

from __future__ import annotations

from dataclasses import dataclass

from pdf2md.schema import TableData


@dataclass
class GridCell:
    text: str  # already escaped and script-annotated by the caller
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    header: bool = False


def render_table(table: TableData) -> str:
    if table.has_spanning_cells and table.html:
        return table.html
    return _strip_caption(table.gfm)


def build_html(cells: list[GridCell], nrows: int, ncols: int) -> str:
    grid = {(c.row, c.col): c for c in cells}
    covered = {
        (r, col)
        for c in cells
        for r in range(c.row, c.row + c.row_span)
        for col in range(c.col, c.col + c.col_span)
        if (r, col) != (c.row, c.col)
    }
    rows = []
    for r in range(nrows):
        out = []
        for col in range(ncols):
            if (r, col) in covered:
                continue
            c = grid.get((r, col))
            if c is None:
                out.append("<td></td>")
                continue
            attr = (f' rowspan="{c.row_span}"' if c.row_span > 1 else "") + (
                f' colspan="{c.col_span}"' if c.col_span > 1 else ""
            )
            tag = "th" if c.header else "td"
            out.append(f"<{tag}{attr}>{c.text}</{tag}>")
        rows.append("<tr>" + "".join(out) + "</tr>")
    return "<table><tbody>" + "".join(rows) + "</tbody></table>"


def build_gfm(cells: list[GridCell], nrows: int, ncols: int) -> str:
    if not nrows or not ncols:
        return ""
    grid = {(c.row, c.col): c for c in cells}

    def text_at(r: int, col: int) -> str:
        c = grid.get((r, col))
        return c.text if c else ""

    # GFM has exactly one header row; use the last of the leading all-header rows
    # rather than blindly assuming row 0.
    header_row = 0
    for r in range(nrows):
        if any((grid.get((r, col)) or GridCell("", r, col)).header for col in range(ncols)):
            header_row = r
        else:
            break
    head = "| " + " | ".join(text_at(header_row, col) for col in range(ncols)) + " |"
    sep = "|" + "|".join(["---"] * ncols) + "|"
    body = [
        "| " + " | ".join(text_at(r, col) for col in range(ncols)) + " |"
        for r in range(nrows)
        if r != header_row
    ]
    return "\n".join([head, sep, *body])


def _strip_caption(gfm: str) -> str:
    lines = gfm.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.lstrip().startswith("|")), 0)
    return "\n".join(lines[start:]).strip()
