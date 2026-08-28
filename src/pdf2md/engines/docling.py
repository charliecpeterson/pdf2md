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
import math
from pathlib import Path
from statistics import fmean
import sysconfig

from pdf2md.engines.base import EngineResult
from pdf2md.logging import get_logger
from pdf2md.normalize import normalize_text
from pdf2md.schema import (
    BBox,
    Block,
    BlockType,
    FigureLabels,
    FigureRef,
    RawCell,
    RawTable,
    TableData,
)

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


# Text Docling extracted from inside a figure and attached to the Picture (see
# `_recover_figure_text`): the caption, axis titles, and tick labels. Exact characters when
# the figure is born-digital (PDF text layer), an OCR read when it's a scan — the adapter
# can't cleanly tell which, so the note says "read", not "OCR'd", and confidence stays
# modest since the grouping/ordering into labels is heuristic and the crop stays source.
_FIGURE_TEXT_CONFIDENCE = 0.5
_FIGURE_TEXT_NOTE = (
    "text the engine read inside the figure (caption, axis titles, tick labels) — "
    "verify against the image; the crop is authoritative"
)


def _quality_evidence(report) -> dict[str, object]:
    """Retain Docling's document grades without treating its scores as probabilities."""
    grades: dict[str, str] = {}
    scores: dict[str, float] = {}
    page_counts: dict[str, int] = {}
    aggregation: dict[str, str] = {}
    for name in ("parse", "layout", "ocr"):
        score = float(getattr(report, f"{name}_score"))
        if not math.isfinite(score):
            page_scores = [
                float(getattr(page, f"{name}_score"))
                for page in report.pages.values()
                if math.isfinite(float(getattr(page, f"{name}_score")))
            ]
            if not page_scores:
                continue
            score = fmean(page_scores)
            page_counts[name] = len(page_scores)
            aggregation[name] = "mean_of_page_scores"
        else:
            aggregation[name] = "document_score"
        single_score = type(report)(**{f"{name}_score": score})
        grades[name] = single_score.mean_grade.value
        scores[name] = round(score, 6)
    return {
        "source": "Docling ConversionResult.confidence",
        "calibrated": False,
        "grades": grades,
        "raw_scores": scores,
        "pages_with_scores": page_counts,
        "aggregation": aggregation,
        "note": (
            "Grades use Docling's document score when available, otherwise the mean "
            "retained per-page score. Raw engine scores are not stable probabilities. "
            "Table quality is omitted because its score is not implemented."
        ),
    }


def missing_cuda_python_headers(device: str) -> Path | None:
    """Return the missing header path when Docling formula CUDA would fail to compile."""
    if device not in {"auto", "cuda"}:
        return None
    try:
        import torch
    except ImportError:
        return None
    if not torch.cuda.is_available():
        return None
    header = Path(sysconfig.get_paths()["include"]) / "Python.h"
    return header if not header.is_file() else None


def _bbox_center_inside(outer: BBox, inner: BBox) -> bool:
    """True if inner's center point lies within outer. Both are BOTTOMLEFT prov bboxes
    (y0 >= y1), so bound each axis by min/max rather than assuming which corner is which."""
    cx, cy = (inner.x0 + inner.x1) / 2, (inner.y0 + inner.y1) / 2
    return (
        min(outer.x0, outer.x1) <= cx <= max(outer.x0, outer.x1)
        and min(outer.y0, outer.y1) <= cy <= max(outer.y0, outer.y1)
    )


def _caption_bbox(doc, pic) -> BBox | None:
    """Bbox of a picture's first caption item (BOTTOMLEFT prov, like blocks) so
    `enrich` can font-decode-refill a garbled caption from the glyph layer."""
    caps = getattr(pic, "captions", None) or []
    for ref in caps:
        item = ref.resolve(doc)
        prov = getattr(item, "prov", None)
        if prov:
            b = prov[0].bbox
            return BBox(x0=b.l, y0=b.t, x1=b.r, y1=b.b)
    return None


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
        force_ocr: bool = False,
        skip_ocr: bool = False,
        artifacts_path: str | None = None,
        device: str = "auto",
    ) -> None:
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        missing_header = missing_cuda_python_headers(device) if formula_enrichment else None
        if missing_header is not None:
            raise RuntimeError(
                f"CUDA formula enrichment needs Python development headers ({missing_header}); "
                "install the matching python-devel package or use --no-formula"
            )

        opts = PdfPipelineOptions()
        opts.do_formula_enrichment = formula_enrichment
        # RapidOCR installs its own INFO handler and otherwise prints three model-path
        # messages per converter initialization. Warnings still surface; routine model
        # selection belongs behind pdf2md's concise progress output.
        opts.ocr_options = RapidOcrOptions(
            rapidocr_params={"Global.log_level": "WARNING"}
        )
        # Skip OCR entirely (layout + figure detection still run) when --ocr-page-vlm will
        # transcribe every page itself — Docling's OCR would just be re-done and discarded.
        opts.do_ocr = not skip_ocr
        # Re-OCR the page images instead of trusting the embedded text layer — for a PDF
        # whose "text" is itself degraded OCR ("?3astman" for "Eastman"), which we can't
        # tell from good born-digital text. Downstream treats it as a scan (see GlyphIndex).
        if force_ocr and not skip_ocr:
            opts.ocr_options.force_full_page_ocr = True
        if artifacts_path:
            opts.artifacts_path = artifacts_path
        opts.accelerator_options = AcceleratorOptions(device=AcceleratorDevice(device))
        self._converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )

    def convert(self, pdf_path: Path) -> EngineResult:
        log.info("docling converting %s", pdf_path)
        conversion = self._converter.convert(str(pdf_path))
        doc = conversion.document

        blocks = self._blocks(doc)
        raw_tables: dict[str, RawTable] = {}
        tables = [self._table(doc, t, raw_tables) for t in doc.tables]
        figures = [self._figure(doc, p) for p in doc.pictures]
        self._recover_figure_text(doc, figures)
        page_sizes = {no: (pg.size.width, pg.size.height) for no, pg in doc.pages.items()}

        return EngineResult(
            blocks=blocks,
            tables=tables,
            figures=figures,
            page_sizes=page_sizes,
            engine_versions={"docling": version("docling"), "pdf2md": version("pdf2md")},
            quality_evidence=_quality_evidence(conversion.confidence),
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
            caption_bbox=_caption_bbox(doc, p),
        )

    def _recover_figure_text(self, doc, figures: list[FigureRef]) -> None:
        """Recover a figure's own printed text (caption, axis titles, tick labels) that Docling
        attaches to the Picture instead of the body reading order, so `iterate_items` never
        yields it and it would be silently dropped — the caption included. Collect those text
        items into the figure whose bbox they sit inside, as labels. Runs for every figure:
        born-digital gives exact text-layer characters, a scan gives Docling's OCR; either way
        model-free and the crop stays authoritative. `--figure-labels` supersedes this with a
        dedicated text-layer or vision read when the user asks for it."""
        body = {item.self_ref for item, _ in doc.iterate_items()}
        orphans: list[tuple[int, BBox, str]] = []
        for t in doc.texts:  # doc order puts the caption first
            if t.self_ref in body:
                continue
            page, bbox = _prov(t)
            text = normalize_text(getattr(t, "text", "") or "")
            if page is not None and bbox is not None and text:
                orphans.append((page, bbox, text))
        if not orphans:
            return
        for fig in figures:
            if fig.bbox is None:
                continue
            inside = [txt for pg, b, txt in orphans
                      if pg == fig.page and _bbox_center_inside(fig.bbox, b)]
            if inside:
                fig.labels = FigureLabels(
                    text="\n".join(inside),
                    confidence=_FIGURE_TEXT_CONFIDENCE,
                    note=_FIGURE_TEXT_NOTE,
                )
