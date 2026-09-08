"""Deciding what gets a crop, and what a crop means once it has one.

`render.py` turns a box into a PNG; this decides which boxes deserve one and what
the resulting image claims. The distinction that matters is `crop_path` versus
`TableData.source_crop`: every table gets a `source_crop` so a reader can check
the printed region, and only the tables whose image is *authoritative* get a
`crop_path`. That key routes the emitter to publish the image instead of the
cells, and marks the block source-dependent for conservation, passages and
chunks — so giving a usable grid a `crop_path` silently withdraws its data.
"""

from __future__ import annotations

from pathlib import Path

from pdf2md.confidence import RECOVER_BELOW
from pdf2md.config import Config
from pdf2md.logging import get_logger
from pdf2md.render import CropRenderer, dpi_for_region
from pdf2md.schema import Block, BlockType
from pdf2md.table_rebuild import glyph_unbacked_tables

log = get_logger("crops")

def _eq_crops(blocks) -> list:
    """Equations whose text is unreliable or absent, so the image crop is the faithful source:
    a low-confidence reading (garbled/scrambled LaTeX), or no text at all — the `--no-formula`
    case, where the region was never transcribed and would otherwise render as an empty marker
    with its content lost. Either way the crop, not the text, is authoritative. In formula-on
    runs every equation has text, so only the low-confidence clause fires there."""
    return [
        b for b in blocks
        if b.type is BlockType.EQUATION and b.bbox is not None
        and ((b.confidence is not None and b.confidence < RECOVER_BELOW) or not b.text.strip())
    ]

def _placeable(flags, emission_index: dict[str, dict]) -> list:
    """Flags whose block has somewhere in the Markdown to put a marker.

    A block can be measured and still have no span: a title heading is consumed
    into the front matter rather than emitted as body. Conservation flags are
    deliberately not filtered here — a conservation finding on an unplaced block
    is a contradiction the annotation pass should raise on."""
    return [
        flag for flag in flags
        if (entry := emission_index.get(flag.block_id))
        and entry.get("start") is not None
        and entry.get("markdown")
    ]

def _table_crops(blocks, tables, *, include_structured: bool = False) -> tuple[list, set]:
    """Every table block gets a crop of its own region, so a reader can check the
    printed table without opening the PDF. The returned id set is the subset
    where that image is *authoritative* rather than a reference: tables Docling
    failed to parse into cells (kept a bbox but no renderable content, would
    otherwise drop), tables on an OCR'd scan page (the cells are OCR guesses, so
    the scan pixels are the ground truth), and tables whose cells the glyph check
    found unbacked (a vision model read a raster table — no embedded text exists
    behind the cells). `include_structured` adds every table to that set for
    --table-ocr, whose independent reader reads the crop."""
    rendered = {t.block_id for t in tables if (t.gfm or "").strip() or t.html}
    unbacked = glyph_unbacked_tables(tables)
    selected, authoritative = [], set()
    for b in blocks:
        # Key on type, not the `#/tables/` id prefix: --ocr-page-vlm repurposes a table block's
        # id into the page-transcription paragraph, which must NOT be cropped as a table.
        if b.bbox is None or b.type is not BlockType.TABLE:
            continue
        selected.append(b)
        if include_structured or b.id not in rendered or b.extra.get("ocr"):
            authoritative.add(b.id)
        elif b.id in unbacked:
            b.extra["cells_unverified"] = True
            authoritative.add(b.id)
    return selected, authoritative

def _attach_table_crops(blocks, tables, authoritative: set) -> None:
    """Hand each table its own crop, and keep `crop_path` for the tables whose
    image is the authority. That key is what tells the emitter to publish the
    image instead of the cells, and what marks the block source-dependent for
    conservation and passages — a usable grid must not carry it."""
    crops = {
        b.id: b.extra["crop_path"] for b in blocks
        if b.type is BlockType.TABLE and b.extra.get("crop_path")
    }
    for table in tables:
        table.source_crop = crops.get(table.block_id, "")
    for b in blocks:
        if b.type is BlockType.TABLE and b.id not in authoritative:
            b.extra.pop("crop_path", None)

def _render_pages(pdf_path: Path, pages: set[int], assets: Path, config: Config) -> dict[int, str]:
    """Render each scanned page to assets/page_NNN.png; returns page -> asset relpath."""
    if not pages:
        return {}
    rasters: dict[int, str] = {}
    with CropRenderer(pdf_path, dpi=config.page_image_dpi) as cr:
        for p in sorted(pages):
            name = f"page_{p:03d}.png"
            try:
                cr.full_page(p, assets / name)
            except Exception as exc:  # noqa: BLE001 - one bad page shouldn't abort the run
                log.warning("page raster failed for page %d: %s", p, exc)
                continue
            rasters[p] = f"assets/{name}"
    return rasters

def _render_crops(pdf_path: Path, figures, eq_blocks, assets: Path, config: Config) -> None:
    if not figures and not eq_blocks:
        return
    with CropRenderer(pdf_path, dpi=config.crop_dpi, padding_pts=config.crop_padding_pts) as cr:
        for fig in figures:
            if fig.bbox is None:
                continue
            name = f"{fig.block_id.strip('#/').replace('/', '_')}_p{fig.page}.png"
            try:
                cr.crop(fig.page, fig.bbox, assets / name,
                        dpi=dpi_for_region(fig.bbox, config.figure_crop_target_px, config.crop_dpi))
                fig.asset_path = f"assets/{name}"
            except Exception as exc:  # noqa: BLE001 - page-level isolate-and-flag
                log.warning("crop failed for %s: %s", fig.block_id, exc)
        for b in eq_blocks:
            name = f"{b.id.strip('#/').replace('/', '_')}_p{b.page}.png"
            try:
                cr.crop(b.page, b.bbox, assets / name,
                        dpi=_block_crop_dpi(b, config))
                b.extra["crop_path"] = f"assets/{name}"
            except Exception as exc:  # noqa: BLE001 - page-level isolate-and-flag
                log.warning("equation crop failed for %s: %s", b.id, exc)

def _block_crop_dpi(block: Block, config: Config) -> int:
    floor = max(config.crop_dpi, config.scan_crop_dpi) if block.extra.get("ocr") else config.crop_dpi
    return dpi_for_region(block.bbox, config.vlm_crop_target_px, floor)
