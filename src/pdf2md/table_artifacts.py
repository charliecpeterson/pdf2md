"""Write inspectable table candidates and normalized repeated-panel data.

Scanned-page values stay OCR candidates. This module preserves their source block,
authoritative crop, raw grid, panel stitching, and review signals without changing
the serializer's coverage decision.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from pdf2md.logging import Progress
from pdf2md.schema import CoverageFlag, Document, TableData
from pdf2md.table_panels import _write_normalized_panels
from pdf2md.table_resolution import enrich_normalized_datasets
from pdf2md.table_verify import write_cell_evidence
from pdf2md.tables import gfm_rows, render_table, table_has_content


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
