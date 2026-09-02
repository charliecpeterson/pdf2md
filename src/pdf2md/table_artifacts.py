"""Write inspectable table candidates and normalized repeated-panel data.

Scanned-page values stay OCR candidates. This module preserves their source block,
authoritative crop, raw grid, panel stitching, and review signals without changing
the serializer's coverage decision.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from pdf2md.logging import Progress
from pdf2md.schema import Block, CoverageFlag, Document, TableData
from pdf2md.table_resolution import enrich_normalized_datasets
from pdf2md.table_verify import typed_value, write_cell_evidence
from pdf2md.tables import gfm_rows, render_table, split_repeated_panels, table_has_content


_ELEMENTS = (
    "", "H", "HE", "LI", "BE", "B", "C", "N", "O", "F", "NE", "NA", "MG",
    "AL", "SI", "P", "S", "CL", "AR", "K", "CA", "SC", "TI", "V", "CR", "MN",
    "FE", "CO", "NI", "CU", "ZN", "GA", "GE", "AS", "SE", "BR", "KR", "RB",
    "SR", "Y", "ZR", "NB", "MO", "TC", "RU", "RH", "PD", "AG", "CD", "IN",
    "SN", "SB", "TE", "I", "XE", "CS", "BA", "LA", "CE", "PR", "ND", "PM",
    "SM", "EU", "GD", "TB", "DY", "HO", "ER", "TM", "YB", "LU", "HF", "TA",
    "W", "RE", "OS", "IR", "PT", "AU", "HG", "TL", "PB", "BI", "PO", "AT",
    "RN",
)
_ATOMIC_NUMBER = {symbol: number for number, symbol in enumerate(_ELEMENTS) if symbol}


def write_table_artifacts(
    doc: Document,
    version_dir: Path,
    table_ocr_executable: str | None = None,
    table_reference_path: str | None = None,
    progress: Progress | None = None,
) -> None:
    blocks = {block.id: block for block in doc.blocks}
    table_dir = version_dir / "data" / "tables"
    artifact_rows: dict[str, list[list[str]]] = {}
    for table in doc.tables:
        table.candidate_path = ""
        table.data_path = ""
        table.json_path = ""
        table.normalized_data_path = ""
        table.normalized_json_path = ""
        table.cell_evidence_path = ""
        table.cell_evidence_counts = {}
        table.cell_resolution_counts = {}
        table.glyph_grid_path = ""
        table.printed_lines_path = ""
    for table in doc.tables:
        if not table_has_content(table):
            continue
        block = blocks.get(table.block_id)
        stem = table.block_id.strip("#/").replace("/", "_")
        table_dir.mkdir(parents=True, exist_ok=True)

        # Paths first: the artifact headers cross-link to each other.
        if (table.gfm or "").strip():
            candidate = table_dir / f"{stem}.md"
        elif table.html:
            candidate = table_dir / f"{stem}.html"
        else:
            candidate = table_dir / f"{stem}.txt"
        table.candidate_path = candidate.relative_to(version_dir).as_posix()
        glyph_path = table_dir / f"{stem}.glyph.md" if table.glyph_grid else None
        if glyph_path is not None:
            table.glyph_grid_path = glyph_path.relative_to(version_dir).as_posix()
        lines_path = table_dir / f"{stem}.lines.txt" if table.printed_lines else None
        if lines_path is not None:
            table.printed_lines_path = lines_path.relative_to(version_dir).as_posix()

        if candidate.suffix == ".md":
            candidate.write_text(_artifact_header(table) + render_table(table) + "\n")
        elif candidate.suffix == ".html":
            candidate.write_text(_comment_header(table) + table.html + "\n")
        else:
            candidate.write_text(
                _comment_header(table) + (table.preformatted or "") + "\n"
            )
        if glyph_path is not None:
            glyph_path.write_text(_glyph_header(table) + table.glyph_grid + "\n")
        if lines_path is not None:
            lines_path.write_text(_lines_header(table) + table.printed_lines + "\n")

        rows = gfm_rows(table.gfm) if table.gfm else []
        artifact_rows[table.block_id] = rows
        if rows:
            csv_path = table_dir / f"{stem}.csv"
            with csv_path.open("w", newline="") as stream:
                csv.writer(stream).writerows(rows)
            table.data_path = csv_path.relative_to(version_dir).as_posix()

        record = {
            "schema_version": 2,
            "block_id": table.block_id,
            "page": table.page,
            "authority": (
                "ocr_candidate"
                if block is not None and block.extra.get("ocr")
                else "engine_structured"
            ),
            "source_crop": table.source_crop or (
                block.extra.get("crop_path") if block is not None else None
            ),
            "candidate": table.candidate_path,
            "csv": table.data_path or None,
            "rows": rows,
            "grid_audit": table.grid_audit,
            "cell_glyph_check": table.cell_glyph_check,
        }
        json_path = table_dir / f"{stem}.json"
        json_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        table.json_path = json_path.relative_to(version_dir).as_posix()

    _write_normalized_panels(doc.tables, artifact_rows, blocks, table_dir, version_dir)
    write_cell_evidence(
        doc.tables,
        artifact_rows,
        blocks,
        version_dir,
        table_ocr_executable,
        table_reference_path,
        progress,
    )
    enrich_normalized_datasets(doc.tables, version_dir)

    for table in doc.tables:
        if not table.json_path:
            continue
        json_path = version_dir / table.json_path
        record = json.loads(json_path.read_text())
        record["normalized_csv"] = table.normalized_data_path or None
        record["normalized_json"] = table.normalized_json_path or None
        record["cell_evidence"] = table.cell_evidence_path or None
        record["cell_evidence_counts"] = table.cell_evidence_counts
        record["cell_resolution_counts"] = table.cell_resolution_counts
        record["glyph_grid"] = table.glyph_grid_path or None
        json_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")


def _artifact_header(table: TableData) -> str:
    """Provenance and audit status above the extracted grid.

    A file under data/tables/ is read on its own, away from document.md and its
    markers. Without this it presents as a standalone, authoritative table even
    when the run measured content missing from it."""
    lines = [
        f"<!-- pdf2md: block {table.block_id} on source page {table.page}, "
        f"derived from source.pdf. Not a source file. -->",
        "",
        f"Source: [page {table.page}](../../../source.pdf#page={table.page})"
        + (f" · [source crop](../../{table.source_crop})" if table.source_crop else "")
        + (
            f" · [glyph-truth grid]({Path(table.glyph_grid_path).name})"
            if table.glyph_grid_path else ""
        )
        + (
            f" · [printed lines]({Path(table.printed_lines_path).name})"
            if table.printed_lines_path else ""
        ),
        "",
    ]
    for warning in _table_warnings(table):
        lines.extend([f"> **[pdf2md: {warning}]**", ""])
    return "\n".join(lines) + "\n"


def _comment_header(table: TableData) -> str:
    """The same provenance and findings for artifacts that aren't Markdown, where
    a blockquote would show up as literal text."""
    lines = [
        f"pdf2md: block {table.block_id} on source page {table.page}, derived from "
        f"source.pdf (../../../source.pdf#page={table.page}). Not a source file.",
        *(f"pdf2md: {warning}" for warning in _table_warnings(table)),
    ]
    return "".join(f"<!-- {line} -->\n" for line in lines) + "\n"



def _lines_header(table: TableData) -> str:
    """A plain-text file, so nothing re-flows a listing whose meaning is its
    columns. The header is a comment the way the other artifacts' are, but this
    one has to survive being read as source text, so it stays prose."""
    return (
        f"pdf2md: block {table.block_id} on source page {table.page}, read verbatim "
        f"from source.pdf. Not a source file.\n"
        f"The row audit found this table's arrangement contradicted by the page's own "
        f"ink, so these are its printed lines as typeset, in place of a grid that put "
        f"the values in cells they did not come from.\n"
        f"Source: ../../../source.pdf#page={table.page}\n\n"
    )


def _glyph_header(table: TableData) -> str:
    return "\n".join([
        "<!-- pdf2md: this region read straight out of the PDF's glyph layer, in the "
        "engine's columns. Rows are measured, not modelled. -->",
        "",
        f"Glyph-layer reading of the table on [source page {table.page}]"
        f"(../../../source.pdf#page={table.page}), for comparison against the "
        f"engine's grid in `{Path(table.candidate_path).name}`. Its first row heads "
        f"the table only because GFM needs a header row.",
        "",
        "",
    ])


def _table_warnings(table: TableData) -> list[str]:
    """Every audit finding for this table, as marker text."""
    return [
        f"action required ({finding['severity']}): {finding['detail']}"
        for finding in table.grid_audit.get("findings") or []
    ]


def _write_normalized_panels(
    tables: list[TableData],
    artifact_rows: dict[str, list[list[str]]],
    blocks: dict[str, Block],
    table_dir: Path,
    version_dir: Path,
) -> None:
    table_i_pages: dict[int, list[tuple[TableData, list[dict[str, object]]]]] = {}
    page_fragments: dict[int, list[tuple[TableData, list[dict[str, object]]]]] = {}
    layouts = {}
    for table in tables:
        rows = artifact_rows.get(table.block_id, [])
        table_i_panels = _split_table_i_records(rows)
        if table_i_panels:
            table_i_pages.setdefault(table.page, []).append((table, table_i_panels))
            layouts.pop(table.page, None)
            continue
        panels, layout = split_repeated_panels(rows, layouts.get(table.page))
        if not panels:
            layouts.pop(table.page, None)
            continue
        layouts[table.page] = layout
        page_fragments.setdefault(table.page, []).append((table, panels))

    for page, fragments in table_i_pages.items():
        stitched = []
        for table, panels in fragments:
            for panel in panels:
                panel = dict(panel)
                panel["title_source_block_id"] = table.block_id
                panel["rows"] = [
                    {
                        "source_block_id": table.block_id,
                        "source_row": source_row,
                        "cells": cells,
                    }
                    for source_row, cells in zip(panel.pop("source_rows"), panel["rows"])
                ]
                stitched.append(panel)
        stitched.sort(key=lambda panel: (
            panel["metadata"].get("atomic_number") is None,
            panel["metadata"].get("atomic_number") or 0,
        ))
        for panel_index, panel in enumerate(stitched):
            panel["panel"] = panel_index
        _write_panel_dataset(
            page, stitched, [table for table, _ in fragments], blocks, table_dir, version_dir,
            representation="table_i_records",
        )

    for page, fragments in page_fragments.items():
        panel_count = len(fragments[0][1])
        if panel_count < 2 or any(len(panels) != panel_count for _, panels in fragments):
            continue
        stitched = []
        for panel_index in range(panel_count):
            first = fragments[0][1][panel_index]
            stitched.append({
                "panel": panel_index,
                "title": first["title"],
                "title_cells": first["title_cells"],
                "metadata": _panel_metadata(first["title_cells"], first["title"]),
                "columns": first["columns"],
                "source_start": first["source_start"],
                "rows": [
                    {
                        "source_block_id": table.block_id,
                        "source_row": source_row,
                        "cells": row,
                    }
                    for table, panels in fragments
                    for source_row, row in zip(
                        panels[panel_index]["source_rows"],
                        panels[panel_index]["rows"],
                    )
                ],
                "refused_rows": [
                    {"source_block_id": table.block_id, **refusal}
                    for table, panels in fragments
                    for refusal in panels[panel_index].get("refused_rows", [])
                ],
            })
        _write_panel_dataset(
            page, stitched, [table for table, _ in fragments], blocks, table_dir, version_dir,
            representation="repeated_panels", fragments=fragments,
        )


def _split_table_i_records(rows: list[list[str]]) -> list[dict[str, object]]:
    """Parse independently advancing vertical lanes in Table I."""
    header_rows = [
        (row_index, column_index)
        for row_index, row in enumerate(rows)
        for column_index, cell in enumerate(row)
        if row_index > 0 and cell.strip().upper() == "NL"
    ]
    if not header_rows:
        return []

    starts = sorted({column for _, column in header_rows})
    table_width = max((len(row) for row in rows), default=0)
    widths = {
        start: (starts[index + 1] if index + 1 < len(starts) else table_width) - start
        for index, start in enumerate(starts)
    }
    panels = []
    for start in starts:
        lane_headers = [row_index for row_index, column in header_rows if column == start]
        width = widths[start]
        for header_index, row_index in enumerate(lane_headers):
            title = _find_table_i_title(rows, row_index, start, width)
            if title is None:
                continue
            title_cells, metadata, title_row, title_start = title
            has_next_header = header_index + 1 < len(lane_headers)
            next_header = lane_headers[header_index + 1] if has_next_header else len(rows)
            data_end = next_header - 1 if has_next_header else next_header
            data_rows = [
                (row[start:start + width] + [""] * width)[:width]
                for row in rows[row_index + 1:data_end]
            ]
            panels.append({
                "title": " ".join(
                    str(metadata[key]) for key in (
                        "atomic_number", "symbol", "term", "configuration"
                    ) if metadata[key] is not None
                ),
                "title_cells": title_cells,
                "metadata": metadata,
                "columns": (rows[row_index][start:start + width] + [""] * width)[:width],
                "rows": data_rows,
                "source_rows": list(range(row_index + 1, data_end)),
                "source_start": start,
                "title_source_row": title_row,
                "title_source_start": title_start,
            })
    return panels


def _find_table_i_title(
    rows: list[list[str]], header_row: int, start: int, width: int
) -> tuple[list[str], dict[str, object], int, int] | None:
    for row_index in range(header_row - 1, max(-1, header_row - 4), -1):
        row = rows[row_index]
        for title_start in (start, start - 1):
            if title_start < 0:
                continue
            title_cells = (row[title_start:title_start + width] + [""] * width)[:width]
            metadata = _panel_metadata(title_cells, " ".join(title_cells))
            if metadata["atomic_number"] is not None and metadata["symbol"] is not None:
                return title_cells, metadata, row_index, title_start
    return None


def _panel_metadata(title_cells: list[str] | tuple[str, ...], title: str) -> dict[str, object]:
    cells = [cell.strip() for cell in title_cells if cell.strip()]
    atomic_number = None
    symbol = None
    term = None
    configuration = None
    configuration_index = next(
        (index for index, cell in enumerate(cells) if cell.startswith("(")), None
    )
    if configuration_index is not None and configuration_index >= 1:
        configuration = cells[configuration_index]
        preceding = cells[configuration_index - 1].upper()
        if preceding in _ATOMIC_NUMBER:
            symbol = preceding
            number_index = configuration_index - 2
        else:
            term = cells[configuration_index - 1]
            symbol_cell = cells[configuration_index - 2] if configuration_index >= 2 else ""
            if re.fullmatch(r"[A-Za-z]{1,3}", symbol_cell):
                symbol = symbol_cell.upper()
            number_index = configuration_index - 3
        if number_index >= 0 and cells[number_index].isdigit():
            atomic_number = int(cells[number_index])
        elif symbol in _ATOMIC_NUMBER:
            atomic_number = _ATOMIC_NUMBER[symbol]
    if atomic_number is None:
        match = re.search(r"ATOMIC NUMBER\s+(\d+)", title, re.IGNORECASE)
        if match:
            atomic_number = int(match.group(1))
    if atomic_number is not None and 0 < atomic_number < len(_ELEMENTS):
        symbol = _ELEMENTS[atomic_number]
    return {
        "atomic_number": atomic_number,
        "symbol": symbol,
        "term": term,
        "configuration": configuration,
    }


def _write_panel_dataset(
    page: int,
    panels: list[dict[str, object]],
    tables: list[TableData],
    blocks: dict[str, Block],
    table_dir: Path,
    version_dir: Path,
    representation: str,
    fragments: list[tuple[TableData, list[dict[str, object]]]] | None = None,
) -> None:
    source_blocks = [table.block_id for table in tables]
    authority = (
        "ocr_candidate"
        if any(
            blocks.get(block_id) and blocks[block_id].extra.get("ocr")
            for block_id in source_blocks
        )
        else "engine_structured"
    )
    stem = f"page_{page:03d}_panels"
    csv_path = table_dir / f"{stem}.csv"
    with csv_path.open("w", newline="") as stream:
        fieldnames = [
            "panel", "title", "atomic_number", "symbol", "term", "configuration",
            "row_key", "column", "value", "raw_value", "numeric_value", "value_status",
            "source_block_id", "source_row", "source_column",
        ]
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for panel in panels:
            columns = panel["columns"]
            metadata = panel.get("metadata") or {}
            source_start = panel.get("source_start", 0)
            seen_properties: set[tuple[str, str]] = set()
            source_rows = []
            if representation == "table_i_records":
                source_rows.append((
                    panel["title_source_block_id"],
                    panel["title_source_row"],
                    panel["title_source_start"],
                    panel["title_cells"],
                    True,
                ))
            source_rows.extend(
                (
                    row["source_block_id"], row["source_row"], source_start,
                    row["cells"], False,
                )
                for row in panel["rows"]
            )
            for source_block_id, source_row, row_start, cells, properties_only in source_rows:
                properties = (
                    _table_i_properties(cells)
                    if representation == "table_i_records"
                    else []
                )
                if properties:
                    for row_key, column_index, raw_value in properties:
                        property_key = (row_key, raw_value)
                        if property_key in seen_properties:
                            continue
                        seen_properties.add(property_key)
                        value, numeric_value, value_status = typed_value(raw_value)
                        writer.writerow({
                            "panel": panel["panel"],
                            "title": panel["title"],
                            **metadata,
                            "row_key": row_key,
                            "column": "value",
                            "value": value,
                            "raw_value": raw_value,
                            "numeric_value": numeric_value,
                            "value_status": value_status,
                            "source_block_id": source_block_id,
                            "source_row": source_row,
                            "source_column": row_start + column_index,
                        })
                    continue
                if properties_only:
                    continue
                row_key = cells[0] if cells else ""
                for column_index, (column, raw_value) in enumerate(
                    zip(columns[1:], cells[1:]), start=1
                ):
                    if not column.strip():
                        continue
                    value, numeric_value, value_status = typed_value(raw_value)
                    writer.writerow({
                        "panel": panel["panel"],
                        "title": panel["title"],
                        **metadata,
                        "row_key": row_key,
                        "column": column,
                        "value": value,
                        "raw_value": raw_value,
                        "numeric_value": numeric_value,
                        "value_status": value_status,
                        "source_block_id": source_block_id,
                        "source_row": source_row,
                        "source_column": row_start + column_index,
                    })

    json_path = table_dir / f"{stem}.json"
    json_path.write_text(json.dumps({
        "schema_version": 2,
        "page": page,
        "representation": representation,
        "authority": authority,
        "source_blocks": source_blocks,
        "panels": panels,
        "checks": _panel_checks(panels, fragments or []),
    }, indent=2, ensure_ascii=False) + "\n")
    csv_rel = csv_path.relative_to(version_dir).as_posix()
    json_rel = json_path.relative_to(version_dir).as_posix()
    for table in tables:
        table.normalized_data_path = csv_rel
        table.normalized_json_path = json_rel


def _table_i_properties(cells: list[str]) -> list[tuple[str, int, str]]:
    """Return scalar label/value pairs that do not belong under orbital columns."""
    inline = []
    for index, cell in enumerate(cells):
        label, separator, _ = cell.partition("=")
        value, _, value_status = typed_value(cell)
        if separator and value_status == "numeric":
            inline.append((f"{label.strip()} =", index, cell.strip()))
    labels = [
        index for index, cell in enumerate(cells)
        if cell.strip().endswith("=")
    ]
    properties = list(inline)
    for label_position, label_index in enumerate(labels):
        end = labels[label_position + 1] if label_position + 1 < len(labels) else len(cells)
        value_index = next(
            (
                index for index in range(label_index + 1, end)
                if typed_value(cells[index])[2] == "numeric"
            ),
            None,
        )
        if value_index is not None:
            properties.append((cells[label_index].strip(), value_index, cells[value_index]))
    return properties


def _panel_checks(
    panels: list[dict[str, object]],
    fragments: list[tuple[TableData, list[dict[str, object]]]],
) -> dict[str, object]:
    issues = []
    for table, local_panels in fragments:
        rows_by_source: dict[int, list[str]] = defaultdict(list)
        for panel_index, panel in enumerate(local_panels):
            for refusal in panel.get("refused_rows", []):
                issues.append({
                    "kind": "panel_row_refused",
                    "panel": panel_index,
                    "source_block_id": table.block_id,
                    "source_row": refusal["source_row"],
                    "reason": refusal["reason"],
                })
            for source_row, row in zip(panel["source_rows"], panel["rows"]):
                rows_by_source[source_row].append(row[0] if row else "")
        for source_row, keys in rows_by_source.items():
            if len(set(keys)) > 1:
                issues.append({
                    "kind": "row_key_mismatch",
                    "source_block_id": table.block_id,
                    "row": source_row,
                    "keys": keys,
                })

    for panel in panels:
        previous = None
        for row_index, row in enumerate(panel["rows"]):
            cells = row["cells"]
            try:
                key = float(cells[0])
            except (IndexError, TypeError, ValueError):
                continue
            if previous is not None and key <= previous:
                issues.append({
                    "kind": "nonincreasing_row_key",
                    "panel": panel["panel"],
                    "row": row_index,
                    "previous": previous,
                    "value": key,
                    "source_block_id": row["source_block_id"],
                })
            previous = key
    return {
        "passed": not issues,
        "issues": issues,
        "review_signals": _numeric_review_signals(panels),
    }


def _numeric_review_signals(panels: list[dict[str, object]]) -> list[dict[str, object]]:
    """Flag isolated numeric spikes for review; never infer or replace the source value."""
    signals = []
    for panel in panels:
        columns = panel["columns"]
        rows = panel["rows"]
        for column_index, column_name in enumerate(columns[1:], start=1):
            for row_index in range(2, len(rows) - 2):
                window = []
                for offset in range(-2, 3):
                    try:
                        cells = rows[row_index + offset]["cells"]
                        float(cells[0])
                        window.append(float(cells[column_index]))
                    except (IndexError, TypeError, ValueError):
                        break
                if len(window) != 5:
                    continue
                residual = abs(window[2] - (window[1] + window[3]) / 2)
                neighboring_step = max(
                    abs(window[1] - window[0]),
                    abs(window[4] - window[3]),
                    1e-12,
                )
                score = residual / neighboring_step
                if score < 2:
                    continue
                row = rows[row_index]
                signals.append({
                    "kind": "local_numeric_spike",
                    "panel": panel["panel"],
                    "row": row_index,
                    "row_key": row["cells"][0],
                    "column": column_name,
                    "value": row["cells"][column_index],
                    "score": round(score, 3),
                    "source_block_id": row["source_block_id"],
                })
        signals.extend(_column_consistency_signals(panel))
    return signals


def _column_consistency_signals(panel: dict[str, object]) -> list[dict[str, object]]:
    signals = []
    columns = panel["columns"]
    rows = panel["rows"]
    for column_index, column_name in enumerate(columns[1:], start=1):
        if not column_name.strip():
            continue
        classified = []
        for row_index, row in enumerate(rows):
            cells = row["cells"]
            raw_value = cells[column_index] if column_index < len(cells) else ""
            _, _, status = typed_value(raw_value)
            if status not in {"blank", "dot_placeholder", "dash_placeholder"}:
                classified.append((row_index, row, raw_value, status))
        numeric = [item for item in classified if item[3] == "numeric"]
        if len(classified) >= 8 and len(numeric) / len(classified) >= 0.9:
            for row_index, row, raw_value, status in classified:
                if status == "numeric":
                    continue
                signals.append({
                    "kind": "numeric_type_outlier",
                    "panel": panel["panel"],
                    "row": row_index,
                    "row_key": row["cells"][0] if row["cells"] else "",
                    "column": column_name,
                    "value": raw_value,
                    "source_block_id": row["source_block_id"],
                })

        decimal_places = Counter(
            len(raw_value.rsplit(".", 1)[1]) if "." in raw_value else 0
            for _, _, raw_value, _ in numeric
            if "e" not in raw_value.lower()
        )
        if len(numeric) < 8 or not decimal_places:
            continue
        expected, count = decimal_places.most_common(1)[0]
        if count / len(numeric) < 0.8:
            continue
        for row_index, row, raw_value, _ in numeric:
            actual = len(raw_value.rsplit(".", 1)[1]) if "." in raw_value else 0
            if actual == expected:
                continue
            signals.append({
                "kind": "decimal_place_outlier",
                "panel": panel["panel"],
                "row": row_index,
                "row_key": row["cells"][0] if row["cells"] else "",
                "column": column_name,
                "value": raw_value,
                "expected_decimal_places": expected,
                "actual_decimal_places": actual,
                "source_block_id": row["source_block_id"],
            })
    return signals


def annotate_table_artifacts(
    version_dir: Path, doc: Document, flags: list[CoverageFlag]
) -> int:
    """Carry post-emission findings into every artifact derived from a table.

    `annotate_conservation_warnings` places these in the Markdown a reader
    browses, but data/tables/*.md and *.json are read on their own, where an
    unmarked grid presents as authoritative. A finding on the table's own block
    reproduces in full; findings elsewhere on its page become a pointer, because
    a page-level loss is the kind that shows up in a neighbouring artifact."""
    by_block: dict[str, list[CoverageFlag]] = defaultdict(list)
    by_page: Counter[int] = Counter()
    for flag in flags:
        by_block[flag.block_id].append(flag)
        if flag.disposition == "action_required":
            by_page[flag.page] += 1

    annotated = 0
    for table in doc.tables:
        mine = by_block.get(table.block_id, [])
        # The structural findings are already spelled out in the artifact's own
        # header; repeating their summary flag here would say it twice. They
        # still count as this table's own, or they'd read as findings elsewhere.
        own = [
            flag for flag in mine if not flag.reason.startswith("table structure:")
        ]
        elsewhere = by_page[table.page] - sum(
            1 for flag in mine if flag.disposition == "action_required"
        )
        warnings = [f"action required ({flag.severity}): {flag.reason}" for flag in own]
        if elsewhere > 0:
            warnings.append(
                f"source page {table.page} has {elsewhere} other finding(s) "
                f"requiring review; see review.md"
            )
        if not warnings:
            continue
        annotated += 1
        if table.candidate_path:
            _insert_warnings(version_dir / table.candidate_path, warnings, table.page)
        if table.json_path:
            path = version_dir / table.json_path
            record = json.loads(path.read_text())
            record["post_emission_warnings"] = warnings
            path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return annotated


def _insert_warnings(path: Path, warnings: list[str], page: int) -> None:
    """Place the markers above the grid, below the artifact's own header."""
    if not path.is_file():
        return
    lines = path.read_text().splitlines()
    grid = next(
        (i for i, line in enumerate(lines) if line.lstrip().startswith(("|", "<table"))),
        len(lines),
    )
    markdown = path.suffix == ".md"
    source = f"[source page {page}](../../../source.pdf#page={page})" if markdown else (
        f"source page {page} (../../../source.pdf#page={page})"
    )
    markers: list[str] = []
    for warning in warnings:
        text = f"pdf2md: {warning}; verify against {source}"
        markers.extend([f"> **[{text}]**", ""] if markdown else [f"<!-- {text} -->"])
    path.write_text("\n".join(lines[:grid] + markers + lines[grid:]) + "\n")
