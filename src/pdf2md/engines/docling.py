"""Docling adapter: translate a DoclingDocument into pdf2md's schema.

The only module that imports docling. Blocks come out in Docling's reading order;
tables and figures are matched back to their blocks by `self_ref`.
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from pdf2md.engines.base import EngineResult
from pdf2md.logging import get_logger
from pdf2md.schema import BBox, Block, BlockType, FigureRef, TableData

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


class DoclingEngine:
    name = "docling"

    def __init__(self, *, formula_enrichment: bool = True, artifacts_path: str | None = None) -> None:
        # Imported lazily so the rest of pdf2md never pays the docling import cost.
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        # Formula enrichment turns display equations into LaTeX rather than empty
        # blocks. It is accurate but slow, so it is caller-controlled.
        opts = PdfPipelineOptions()
        opts.do_formula_enrichment = formula_enrichment
        if artifacts_path:  # load models from a local snapshot instead of downloading
            opts.artifacts_path = artifacts_path
        self._converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )

    def convert(self, pdf_path: Path) -> EngineResult:
        log.info("docling converting %s", pdf_path)
        doc = self._converter.convert(str(pdf_path)).document

        blocks: list[Block] = []
        for item, _level in doc.iterate_items():
            value = _label_value(item)
            btype = _LABEL_MAP.get(value, BlockType.OTHER)
            page, bbox = _prov(item)
            if page is None:
                continue
            extra = {}
            level = getattr(item, "level", None)
            if level is not None:
                extra["level"] = level
            blocks.append(
                Block(
                    id=item.self_ref,
                    type=btype,
                    text=(getattr(item, "text", "") or ""),
                    page=page,
                    bbox=bbox,
                    engine=self.name,
                    extra=extra,
                )
            )

        tables = [self._table(doc, t) for t in doc.tables]
        figures = [self._figure(doc, p) for p in doc.pictures]
        page_sizes = {
            no: (pg.size.width, pg.size.height) for no, pg in doc.pages.items()
        }
        return EngineResult(
            blocks=blocks,
            tables=tables,
            figures=figures,
            page_sizes=page_sizes,
            engine_versions={"docling": version("docling"), "pdf2md": version("pdf2md")},
        )

    def _table(self, doc, t) -> TableData:
        page, bbox = _prov(t)
        spanning = any(
            getattr(c, "row_span", 1) > 1 or getattr(c, "col_span", 1) > 1
            for c in getattr(t.data, "table_cells", [])
        )
        return TableData(
            block_id=t.self_ref,
            page=page or 0,
            bbox=bbox,
            gfm=t.export_to_markdown(doc),
            html=t.export_to_html(doc) if spanning else None,
            has_spanning_cells=spanning,
        )

    def _figure(self, doc, p) -> FigureRef:
        page, bbox = _prov(p)
        caption = p.caption_text(doc) if hasattr(p, "caption_text") else None
        return FigureRef(
            block_id=p.self_ref,
            page=page or 0,
            bbox=bbox,
            caption=caption or None,
        )
