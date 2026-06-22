"""Docling adapter: translate a DoclingDocument into pdf2md's schema.

The only module that imports docling, and pure translation — no pdfium, no
verification. Blocks come out in Docling's reading order; tables and figures are
matched back to their blocks by `self_ref`; text is normalized (Greek-letter glyph
names, orphan combining marks). The ligature/script/equation verification — which
needs glyph geometry — runs afterwards in `enrich`, off the engine, so any engine
inherits it. Tables ship their structured cells (`RawTable`) for `enrich` to rebuild.
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from pdf2md.engines.base import EngineResult
from pdf2md.logging import get_logger
from pdf2md.normalize import normalize_text
from pdf2md.schema import BBox, Block, BlockType, FigureRef, RawCell, RawTable, TableData

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


def _cell_bbox(cell, page_height: float | None) -> BBox | None:
    b = getattr(cell, "bbox", None)
    if b is None:
        return None
    # Table-cell bboxes come in TOPLEFT origin, unlike block prov bboxes (BOTTOMLEFT,
    # y0>y1). Flip Y so a cell bbox matches pdfium's coordinate space — otherwise
    # enrich's glyph lookups (script overlay, font-decode refill) land on the wrong
    # part of the page.
    if page_height is not None and getattr(getattr(b, "coord_origin", None), "name", "") == "TOPLEFT":
        return BBox(x0=b.l, y0=page_height - b.t, x1=b.r, y1=page_height - b.b)
    return BBox(x0=b.l, y0=b.t, x1=b.r, y1=b.b)


def _raw_cell(c, page_height: float | None) -> RawCell:
    return RawCell(
        text=normalize_text(getattr(c, "text", "") or ""),
        bbox=_cell_bbox(c, page_height),
        row=c.start_row_offset_idx,
        col=c.start_col_offset_idx,
        row_span=c.end_row_offset_idx - c.start_row_offset_idx,
        col_span=c.end_col_offset_idx - c.start_col_offset_idx,
        header=getattr(c, "column_header", False) or getattr(c, "row_header", False),
    )


class DoclingEngine:
    name = "docling"

    def __init__(
        self,
        *,
        formula_enrichment: bool = True,
        artifacts_path: str | None = None,
    ) -> None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

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

        blocks = self._blocks(doc)
        raw_tables: dict[str, RawTable] = {}
        tables = [self._table(doc, t, raw_tables) for t in doc.tables]
        figures = [self._figure(doc, p) for p in doc.pictures]
        page_sizes = {no: (pg.size.width, pg.size.height) for no, pg in doc.pages.items()}

        return EngineResult(
            blocks=blocks,
            tables=tables,
            figures=figures,
            page_sizes=page_sizes,
            engine_versions={"docling": version("docling"), "pdf2md": version("pdf2md")},
            raw_tables=raw_tables,
        )

    def _blocks(self, doc) -> list[Block]:
        """Pure Docling -> schema translation. The verification layer (scripts,
        ligatures, equation cross-check, OCR detection) runs afterwards in
        `enrich.enrich_blocks`, off the engine, so any engine inherits it."""
        blocks: list[Block] = []
        for item, _level in doc.iterate_items():
            btype = _LABEL_MAP.get(_label_value(item), BlockType.OTHER)
            page, bbox = _prov(item)
            if page is None:
                continue
            raw = getattr(item, "text", "") or ""
            text = normalize_text(raw)
            # A block whose only content was extraction noise (an orphaned
            # combining mark) is now empty; skip it rather than emit a stray
            # glyph. Genuinely empty blocks (raw already blank) still flow
            # through to the emitter's empty-block marker.
            if raw.strip() and not text.strip():
                continue
            extra: dict = {}
            level = getattr(item, "level", None)
            if level is not None:
                extra["level"] = level
            blocks.append(
                Block(id=item.self_ref, type=btype, text=text, page=page, bbox=bbox,
                      engine=self.name, extra=extra)
            )
        return blocks

    def _table(self, doc, t, raw_tables: dict[str, RawTable]) -> TableData:
        """Translate a table: Docling's own rendering as the fallback markup, plus
        the structured cells for `enrich` to rebuild with recovered scripts."""
        page, bbox = _prov(t)
        ph = doc.pages[page].size.height if page is not None and page in doc.pages else None
        data = getattr(t, "data", None)
        cells = getattr(data, "table_cells", None) if data else None
        spanning = any(c.row_span > 1 or c.col_span > 1 for c in cells) if cells else False
        if cells:
            raw_tables[t.self_ref] = RawTable(
                cells=[_raw_cell(c, ph) for c in cells],
                num_rows=data.num_rows, num_cols=data.num_cols,
            )
        return TableData(
            block_id=t.self_ref, page=page or 0, bbox=bbox,
            gfm=normalize_text(t.export_to_markdown(doc)),
            html=normalize_text(t.export_to_html(doc)) if spanning else None,
            has_spanning_cells=spanning,
        )

    def _figure(self, doc, p) -> FigureRef:
        page, bbox = _prov(p)
        caption = p.caption_text(doc) if hasattr(p, "caption_text") else None
        return FigureRef(
            block_id=p.self_ref, page=page or 0, bbox=bbox,
            caption=normalize_text(caption) if caption else None,
        )
