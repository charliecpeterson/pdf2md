"""Write bounded, source-addressable text chunks for agent retrieval.

Chunks reuse the document model and point back to Markdown, assets, and source
pages, plus the union bbox of their blocks so an answer can cite an exact page
region. They are an index artifact, not another copy of full provenance.
"""

from __future__ import annotations

import json
from pathlib import Path

from pdf2md.confidence import plot_data_accepted
from pdf2md.schema import (
    Block,
    BlockType,
    CoverageStatus,
    Document,
    FigureRef,
    Section,
    TableData,
)


CHUNKS_SCHEMA_VERSION = 2
DEFAULT_MAX_CHARS = 6000


def section_map(doc: Document, md_files: list[Path]) -> dict[str, tuple[str, str, str]]:
    names = [path.name for path in md_files if path.name != "index.md"]
    mapping: dict[str, tuple[str, str, str]] = {}

    def assign(section: Section, markdown: str) -> None:
        for block_id in section.block_ids:
            mapping[block_id] = (section.id, section.title, markdown)
        for child in section.children:
            assign(child, markdown)

    if not names:
        return mapping
    if len(names) == 1:
        assign(doc.sections, names[0])
        return mapping

    name_index = 0
    if doc.sections.block_ids:
        assign(doc.sections, names[name_index])
        name_index += 1
    for section, name in zip(doc.sections.children, names[name_index:]):
        assign(section, name)
    return mapping


def _figure_text(figure: FigureRef) -> str:
    parts = [value for value in (figure.caption, figure.description) if value]
    if figure.data_extraction_status and figure.data_extraction_status != "not_attempted":
        status = f"Figure data extraction: {figure.data_extraction_status}."
        if figure.data_extraction_note:
            status += f" {figure.data_extraction_note}"
        parts.append(status)
    if figure.labels and figure.labels.text:
        parts.append(figure.labels.text)
    if plot_data_accepted(figure.digitization):
        names = figure.digitization.series_names or []
        for index, series in enumerate(figure.digitization.series, start=1):
            name = names[index - 1] if index <= len(names) else f"series {index}"
            rows = [f"{x:g},{y:g}" for x, y in series]
            parts.append(f"Chart data, {name}:\nx,y\n" + "\n".join(rows))
    return "\n\n".join(parts)


def block_content(
    block: Block,
    tables: dict[str, TableData],
    figures: dict[str, FigureRef],
) -> tuple[str, list[str]]:
    if block.extra.get("figure_caption_of"):
        return "", []
    assets: list[str] = []
    crop = block.extra.get("crop_path")
    if crop:
        assets.append(crop)

    table = tables.get(block.id)
    if table is not None:
        assets.extend(path for path in (
            table.candidate_path,
            table.data_path,
            table.json_path,
            table.normalized_data_path,
            table.normalized_json_path,
            table.cell_evidence_path,
        ) if path)
        if table.preformatted:
            return table.preformatted, assets
        if table.has_spanning_cells and table.html:
            return table.html, assets
        return table.gfm or table.html or "", assets

    figure = figures.get(block.id)
    if figure is not None:
        assets.extend(path for path in (
            figure.asset_path,
            figure.svg_path,
            figure.data_path,
            figure.code_path,
        ) if path)
        if figure.digitization and figure.digitization.verify_asset:
            assets.append(figure.digitization.verify_asset)
        text = _figure_text(figure)
        if not text and assets:
            text = f"[Image-only figure on page {block.page}; inspect the linked asset.]"
        return text, assets

    if block.type is BlockType.EQUATION:
        text = block.extra.get("transcribed") or block.text
        if text.strip():
            return f"$$\n{text}\n$$", assets
        if assets:
            return f"[Image-backed equation on page {block.page}; inspect the linked crop.]", assets
        return "", assets
    text = block.text.strip()
    if not text and assets:
        text = f"[Image-backed {block.type.value} on page {block.page}; inspect the linked crop.]"
    return text, assets


def split_text(text: str, max_chars: int) -> list[str]:
    remaining = text.strip()
    parts: list[str] = []
    while len(remaining) > max_chars:
        cut = remaining.rfind("\n\n", 0, max_chars + 1)
        if cut < max_chars // 2:
            cut = remaining.rfind("\n", 0, max_chars + 1)
        if cut < max_chars // 2:
            cut = remaining.rfind(" ", 0, max_chars + 1)
        if cut < 1:
            cut = max_chars
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def write_chunks(
    version_dir: Path,
    doc: Document,
    md_files: list[Path],
    page_rasters: dict[int, str],
    *,
    emission_index: dict[str, dict] | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> Path:
    if max_chars < 1:
        raise ValueError("max_chars must be positive")

    section_by_block = section_map(doc, md_files)
    tables = {table.block_id: table for table in doc.tables}
    figures = {figure.block_id: figure for figure in doc.figures}
    dispositions_by_block: dict[str, set[str]] = {}
    for flag in doc.coverage.flags if doc.coverage else []:
        dispositions_by_block.setdefault(flag.block_id, set()).add(flag.disposition)

    units = []
    default_markdown = next((path.name for path in md_files if path.name != "index.md"), None)
    for block in doc.blocks:
        if block.type in {BlockType.PAGE_HEADER, BlockType.PAGE_FOOTER}:
            continue
        text, assets = block_content(block, tables, figures)
        if not text:
            continue
        section_id, section_title, markdown = section_by_block.get(
            block.id,
            (doc.sections.id, doc.sections.title, default_markdown),
        )
        markdown = (emission_index or {}).get(block.id, {}).get("markdown", markdown)
        if block.extra.get("ocr") and block.page in page_rasters:
            assets.append(page_rasters[block.page])
        for part in split_text(text, max_chars):
            dispositions = sorted(dispositions_by_block.get(block.id, set()))
            units.append({
                "section_id": section_id,
                "section_title": section_title,
                "markdown": markdown,
                "block_id": block.id,
                "page": block.page,
                "text": part,
                "assets": assets,
                "needs_review": (
                    "action_required" in dispositions
                    or block.coverage_status in {CoverageStatus.FLAGGED, CoverageStatus.DROPPED}
                ),
                "review_dispositions": dispositions,
            })

    chunks = []
    for unit in units:
        current = chunks[-1] if chunks else None
        same_section = current and (
            current["section"]["id"] == unit["section_id"]
            and current["markdown"] == unit["markdown"]
        )
        same_page = current and current["pages"] == [unit["page"]]
        fits = current and len(current["text"]) + 2 + len(unit["text"]) <= max_chars
        if not same_section or not same_page or not fits:
            chunks.append({
                "schema_version": CHUNKS_SCHEMA_VERSION,
                "id": f"chunk-{len(chunks) + 1:06d}",
                "section": {"id": unit["section_id"], "title": unit["section_title"]},
                "markdown": unit["markdown"],
                "pages": [unit["page"]],
                "source_pages": [f"../source.pdf#page={unit['page']}"],
                "block_ids": [unit["block_id"]],
                "assets": list(dict.fromkeys(unit["assets"])),
                "needs_review": unit["needs_review"],
                "review_dispositions": unit["review_dispositions"],
                "text": unit["text"],
            })
            continue
        current["text"] += "\n\n" + unit["text"]
        if unit["block_id"] not in current["block_ids"]:
            current["block_ids"].append(unit["block_id"])
        current["assets"] = list(dict.fromkeys([*current["assets"], *unit["assets"]]))
        current["needs_review"] = current["needs_review"] or unit["needs_review"]
        current["review_dispositions"] = sorted(set(
            current["review_dispositions"] + unit["review_dispositions"]
        ))

    path = version_dir / "chunks.jsonl"
    _attach_bboxes(chunks, doc)
    path.write_text("".join(json.dumps(chunk, ensure_ascii=False) + "\n" for chunk in chunks))
    return path


def _attach_bboxes(chunks: list[dict], doc: Document) -> None:
    """Give every chunk the union of its member blocks' bboxes (PDF points,
    bottom-left origin like everywhere else in provenance), so downstream answers
    can cite the exact region of the source page instead of just the page."""
    blocks_by_id = {b.id: b for b in doc.blocks}
    for chunk in chunks:
        boxes = [
            blocks_by_id[bid].bbox
            for bid in chunk["block_ids"]
            if bid in blocks_by_id and blocks_by_id[bid].bbox is not None
        ]
        if boxes:
            # Inverted-y convention (Docling bottom-left origin): y0 is the UPPER
            # edge and y1 the lower, so the vertical union maxes y0 and mins y1.
            chunk["bbox"] = {
                "x0": min(b.x0 for b in boxes),
                "y0": max(b.y0 for b in boxes),
                "x1": max(b.x1 for b in boxes),
                "y1": min(b.y1 for b in boxes),
            }
