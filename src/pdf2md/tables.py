"""Table markup. `render_table` picks the right serialization of an already-built
table; `build_html`/`build_gfm` assemble markup from a neutral cell grid (used when
the engine adapter rebuilds a table to inject sub/superscripts). Cell text is final
by the time it reaches the builders — the adapter handles escaping and scripts."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from html.parser import HTMLParser

from pdf2md.schema import TableData


@dataclass
class GridCell:
    text: str  # already escaped and script-annotated by the caller
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    header: bool = False


@dataclass(frozen=True)
class RepeatedPanelLayout:
    starts: tuple[int, ...]
    width: int
    titles: tuple[str, ...]
    columns: tuple[tuple[str, ...], ...]
    widths: tuple[int, ...] = ()
    title_cells: tuple[tuple[str, ...], ...] = ()

    def panel_width(self, index: int) -> int:
        return self.widths[index] if self.widths else self.width


class _HTMLTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


def html_tables(html: str) -> list[list[list[str]]]:
    parser = _HTMLTableParser()
    parser.feed(html)
    return parser.tables


def render_table(table: TableData) -> str:
    if table.has_spanning_cells and table.html:
        return table.html
    return _strip_caption(table.gfm)


def table_has_content(table: TableData) -> bool:
    return bool((table.gfm or "").strip() or table.html or table.preformatted)


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


def gfm_rows(gfm: str) -> list[list[str]]:
    """Return the printed cell strings from a GFM table, excluding its separator row."""
    rows: list[list[str]] = []
    for line in _strip_caption(gfm).splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        body = stripped[1:-1] if stripped.endswith("|") else stripped[1:]
        cells = [cell.strip().replace(r"\|", "|") for cell in re.split(r"(?<!\\)\|", body)]
        compact = [cell.replace(" ", "") for cell in cells]
        if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in compact):
            continue
        rows.append(cells)
    return rows


def split_repeated_panels(
    rows: list[list[str]],
    previous: RepeatedPanelLayout | None = None,
) -> tuple[list[dict[str, object]], RepeatedPanelLayout | None]:
    """Split repeated schemas while preserving each panel's independent source rows."""
    layout = None
    data_start = 0
    for row_index, row in enumerate(rows):
        positions: dict[str, list[int]] = defaultdict(list)
        for column, cell in enumerate(row):
            key = " ".join(cell.lower().split())
            if key and any(character.isalpha() for character in key):
                positions[key].append(column)
        for starts in positions.values():
            if len(starts) < 2 or starts[0] != 0:
                continue
            widths = [right - left for left, right in zip(starts, starts[1:])]
            widths.append(len(row) - starts[-1])
            if min(widths) < 2:
                continue
            title_row = rows[row_index - 1] if row_index else []
            title_cells = tuple(
                tuple(title_row[start:start + widths[index]])
                for index, start in enumerate(starts)
            )
            titles = tuple(
                title_row[start].strip() if start < len(title_row) else ""
                for start in starts
            )
            columns = tuple(
                tuple(row[start:start + widths[index]])
                for index, start in enumerate(starts)
            )
            layout = RepeatedPanelLayout(
                tuple(starts), widths[0], titles, columns, tuple(widths), title_cells
            )
            data_start = row_index + 1
            break
        if layout is not None:
            break

    if layout is None:
        layout = previous
        expected_width = sum(layout.widths) if layout and layout.widths else (
            layout.width * len(layout.starts) if layout else 0
        )
        if layout is None or any(len(row) != expected_width for row in rows):
            return [], previous

    panel_rows: list[list[list[str]]] = [[] for _ in layout.starts]
    source_rows: list[list[int]] = [[] for _ in layout.starts]
    refused_rows: list[list[dict[str, object]]] = [[] for _ in layout.starts]
    boundary_refusals = _shifted_panel_rows(rows[data_start:], layout, data_start)
    for source_row, row in enumerate(rows[data_start:], start=data_start):
        slices = [
            row[start:start + layout.panel_width(panel_index)]
            for panel_index, start in enumerate(layout.starts)
        ]
        populated = [any(cell.strip() for cell in cells) for cells in slices]
        row_boundary_refusals = boundary_refusals.get(source_row, {})
        has_complete_tail = any(
            cells and cells[-1].strip()
            for cells, present in zip(slices, populated)
            if present
        )
        for panel_index, cells in enumerate(slices):
            if not populated[panel_index]:
                continue
            reason = row_boundary_refusals.get(panel_index)
            width = layout.panel_width(panel_index)
            if reason is None and len(cells) < width:
                reason = "short_panel_row"
            if (
                reason is None
                and has_complete_tail
                and cells
                and not cells[-1].strip()
                and any(cell.strip() for cell in cells[1:-1])
            ):
                reason = "ambiguous_trailing_blank"
            if reason is not None:
                refused_rows[panel_index].append({
                    "source_row": source_row,
                    "reason": reason,
                    "cells": cells,
                })
                continue
            panel_rows[panel_index].append(cells)
            source_rows[panel_index].append(source_row)

    panels = []
    for panel_index, start in enumerate(layout.starts):
        panels.append({
            "title": layout.titles[panel_index],
            "columns": list(layout.columns[panel_index]),
            "rows": panel_rows[panel_index],
            "source_rows": source_rows[panel_index],
            "refused_rows": refused_rows[panel_index],
            "title_cells": (
                list(layout.title_cells[panel_index]) if layout.title_cells else []
            ),
            "source_start": start,
            "source_data_start": data_start,
        })
    return panels, layout


def _shifted_panel_rows(
    rows: list[list[str]], layout: RepeatedPanelLayout, data_start: int
) -> dict[int, dict[int, str]]:
    """Find persistent or unambiguous row-key shifts at repeated-panel boundaries."""
    if len(layout.starts) < 2:
        return {}
    states: dict[tuple[int, int], str] = {}
    for offset, row in enumerate(rows):
        if not row:
            continue
        anchor = row[layout.starts[0]].strip()
        if not anchor or sum(cell.strip() == anchor for cell in row) < 2:
            continue
        for boundary, start in enumerate(layout.starts[1:], start=1):
            if start <= 0 or start - 1 >= len(row) or row[start - 1].strip() != anchor:
                continue
            expected = row[start].strip() if start < len(row) else ""
            states[offset, boundary] = "duplicate" if expected == anchor else "shifted"

    refusals: dict[int, dict[int, str]] = {}
    for (offset, boundary), state in states.items():
        if state == "duplicate" and not any(
            states.get((neighbor, boundary)) == "shifted"
            for neighbor in (offset - 1, offset + 1)
        ):
            continue
        refusals[data_start + offset] = {
            panel_index: "ambiguous_shifted_panel_boundary"
            for panel_index in range(boundary - 1, len(layout.starts))
        }
    return refusals
