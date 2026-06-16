"""Docling adapter: translate a DoclingDocument into pdf2md's schema.

The only module that imports docling. Blocks come out in Docling's reading order;
tables and figures are matched back to their blocks by `self_ref`. On born-digital
pages we overlay inline sub/superscripts (recovered from pypdfium2 glyph geometry,
see `scripts`) and normalize unresolved Greek-letter glyph names (see `normalize`).
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

import pypdfium2 as pdfium

from pdf2md.engines.base import EngineResult
from pdf2md.logging import get_logger
from pdf2md.normalize import unglyph
from pdf2md.schema import BBox, Block, BlockType, FigureRef, TableData
from pdf2md.scripts import PageChars, apply_scripts
from pdf2md.tables import GridCell, build_gfm, build_html

log = get_logger("engines.docling")

# Docling DocItemLabel value -> our BlockType.
_LABEL_MAP = {
    "title": BlockType.HEADING,
    "section_header": BlockType.HEADING,
    "text": BlockType.PARAGRAPH,
    "paragraph": BlockType.PARAGRAPH,
    "list_item": BlockType.LIST,
    "table": BlockType.TABLE,
    "picture": BlockType.FIGURE,
    "formula": BlockType.EQUATION,
    "code": BlockType.CODE,
    "caption": BlockType.CAPTION,
    "footnote": BlockType.FOOTNOTE,
    "page_header": BlockType.PAGE_HEADER,
    "page_footer": BlockType.PAGE_FOOTER,
}

# Block types whose prose carries inline scripts worth recovering.
_SCRIPT_TYPES = {
    BlockType.PARAGRAPH, BlockType.HEADING, BlockType.LIST,
    BlockType.CAPTION, BlockType.FOOTNOTE, BlockType.OTHER,
}


def _label_value(item) -> str:
    label = getattr(item, "label", None)
    return getattr(label, "value", str(label))


def _prov(item) -> tuple[int | None, BBox | None]:
    prov = getattr(item, "prov", None)
    if not prov:
        return None, None
    p = prov[0]
    b = p.bbox
    return p.page_no, BBox(x0=b.l, y0=b.t, x1=b.r, y1=b.b)


def _cell_bbox(cell) -> BBox | None:
    b = getattr(cell, "bbox", None)
    if b is None:
        return None
    return BBox(x0=b.l, y0=b.t, x1=b.r, y1=b.b)


class DoclingEngine:
    name = "docling"

    def __init__(
        self,
        *,
        formula_enrichment: bool = True,
        artifacts_path: str | None = None,
        detect_scripts: bool = True,
    ) -> None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        self._detect_scripts = detect_scripts
        opts = PdfPipelineOptions()
        opts.do_formula_enrichment = formula_enrichment
        if artifacts_path:
            opts.artifacts_path = artifacts_path
        self._converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )

    def convert(self, pdf_path: Path) -> EngineResult:
        log.info("docling converting %s", pdf_path)
        doc = self._converter.convert(str(pdf_path)).document

        pdf = pdfium.PdfDocument(str(pdf_path)) if self._detect_scripts else None
        cache: dict[int, PageChars | None] = {}

        def page_chars(page_no: int | None) -> PageChars | None:
            if pdf is None or page_no is None:
                return None
            if page_no not in cache:
                try:
                    pc = PageChars(pdf[page_no - 1])
                    cache[page_no] = None if pc.empty else pc
                except Exception as exc:  # noqa: BLE001 - geometry is best-effort
                    log.warning("char geometry failed on page %d: %s", page_no, exc)
                    cache[page_no] = None
            return cache[page_no]

        try:
            blocks = self._blocks(doc, page_chars)
            tables = [self._table(doc, t, page_chars) for t in doc.tables]
            figures = [self._figure(doc, p) for p in doc.pictures]
            page_sizes = {no: (pg.size.width, pg.size.height) for no, pg in doc.pages.items()}
        finally:
            if pdf is not None:
                pdf.close()

        return EngineResult(
            blocks=blocks,
            tables=tables,
            figures=figures,
            page_sizes=page_sizes,
            engine_versions={"docling": version("docling"), "pdf2md": version("pdf2md")},
        )

    def _blocks(self, doc, page_chars) -> list[Block]:
        blocks: list[Block] = []
        for item, _level in doc.iterate_items():
            value = _label_value(item)
            btype = _LABEL_MAP.get(value, BlockType.OTHER)
            page, bbox = _prov(item)
            if page is None:
                continue
            text = unglyph(getattr(item, "text", "") or "")
            if btype in _SCRIPT_TYPES and bbox is not None:
                pc = page_chars(page)
                if pc is not None:
                    text = apply_scripts(text, pc.scored_region(bbox))
            extra = {}
            level = getattr(item, "level", None)
            if level is not None:
                extra["level"] = level
            blocks.append(
                Block(id=item.self_ref, type=btype, text=text, page=page, bbox=bbox,
                      engine=self.name, extra=extra)
            )
        return blocks

    def _table(self, doc, t, page_chars) -> TableData:
        page, bbox = _prov(t)
        data = getattr(t, "data", None)
        cells = getattr(data, "table_cells", None) if data else None
        spanning = any(c.row_span > 1 or c.col_span > 1 for c in cells) if cells else False
        pc = page_chars(page)

        gfm = html = None
        if pc is not None and cells:
            rebuilt = build_html(self._grid(data, pc, escape=True), data.num_rows, data.num_cols)
            if "<sub>" in rebuilt or "<sup>" in rebuilt:  # only diverge from Docling when scripts help
                html = rebuilt if spanning else None
                # GFM can't express spans; leave it empty for spanning tables
                # rather than persist a flattened, misleading one.
                gfm = "" if spanning else build_gfm(self._grid(data, pc, escape=False), data.num_rows, data.num_cols)
        if gfm is None and html is None:
            gfm = unglyph(t.export_to_markdown(doc))
            html = unglyph(t.export_to_html(doc)) if spanning else None
        return TableData(
            block_id=t.self_ref, page=page or 0, bbox=bbox,
            gfm=gfm or "", html=html, has_spanning_cells=spanning,
        )

    def _grid(self, data, pc: PageChars, *, escape: bool) -> list[GridCell]:
        out = []
        for c in data.table_cells:
            text = self._cell_text(c, pc, escape=escape)
            if not escape:
                text = text.replace("|", r"\|").replace("\n", " ")
            out.append(
                GridCell(
                    text=text,
                    row=c.start_row_offset_idx,
                    col=c.start_col_offset_idx,
                    row_span=c.end_row_offset_idx - c.start_row_offset_idx,
                    col_span=c.end_col_offset_idx - c.start_col_offset_idx,
                    header=getattr(c, "column_header", False) or getattr(c, "row_header", False),
                )
            )
        return out

    def _cell_text(self, cell, pc: PageChars, *, escape: bool) -> str:
        raw = unglyph(getattr(cell, "text", "") or "")
        cb = _cell_bbox(cell)
        scored = pc.scored_region(cb) if cb is not None else []
        return apply_scripts(raw, scored, escape=escape)

    def _figure(self, doc, p) -> FigureRef:
        page, bbox = _prov(p)
        caption = p.caption_text(doc) if hasattr(p, "caption_text") else None
        return FigureRef(
            block_id=p.self_ref, page=page or 0, bbox=bbox,
            caption=unglyph(caption) if caption else None,
        )
