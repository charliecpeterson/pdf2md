from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

from docsmcp.store.schema import BBox, Block, BlockType, VerifyStatus

VALID_PROFILES = ("fast", "balanced", "max_accuracy")


_DOCLING_LABEL_MAP: dict[str, BlockType] = {
    "title": BlockType.HEADING,
    "section_header": BlockType.HEADING,
    "page_header": BlockType.OTHER,
    "page_footer": BlockType.OTHER,
    "paragraph": BlockType.PARAGRAPH,
    "text": BlockType.PARAGRAPH,
    "list_item": BlockType.LIST,
    "table": BlockType.TABLE,
    "picture": BlockType.FIGURE,
    "figure": BlockType.FIGURE,
    "caption": BlockType.CAPTION,
    "formula": BlockType.EQUATION,
    "equation": BlockType.EQUATION,
    "code": BlockType.CODE,
    "footnote": BlockType.FOOTNOTE,
}


def _block_id(text: str, page: int, idx: int) -> str:
    h = hashlib.sha1(f"{page}:{idx}:{text[:200]}".encode()).hexdigest()
    return h[:12]


def _label_to_type(label: str | None) -> BlockType:
    if not label:
        return BlockType.OTHER
    return _DOCLING_LABEL_MAP.get(label.lower(), BlockType.OTHER)


def _extract_bbox(item: Any) -> BBox | None:
    prov = getattr(item, "prov", None)
    if not prov:
        return None
    first = prov[0] if isinstance(prov, list) and prov else prov
    bbox = getattr(first, "bbox", None)
    if bbox is None:
        return None
    return BBox(
        x0=float(getattr(bbox, "l", 0.0)),
        y0=float(getattr(bbox, "t", 0.0)),
        x1=float(getattr(bbox, "r", 0.0)),
        y1=float(getattr(bbox, "b", 0.0)),
    )


def _extract_page(item: Any) -> int:
    prov = getattr(item, "prov", None)
    if not prov:
        return 0
    first = prov[0] if isinstance(prov, list) and prov else prov
    return int(getattr(first, "page_no", 0))


def _build_pipeline_options(profile: str) -> Any:
    """Build PdfPipelineOptions for the given profile. Returns the options object."""
    from docling.datamodel.pipeline_options import (
        AcceleratorDevice,
        AcceleratorOptions,
        EasyOcrOptions,
        OcrMacOptions,
        PdfPipelineOptions,
    )

    if profile not in VALID_PROFILES:
        raise ValueError(f"Unknown profile: {profile!r}. Valid: {VALID_PROFILES}")

    opts = PdfPipelineOptions()
    opts.accelerator_options = AcceleratorOptions(
        device=AcceleratorDevice.MPS if sys.platform == "darwin" else AcceleratorDevice.AUTO,
        num_threads=8,
    )
    opts.do_table_structure = True
    opts.do_ocr = True

    if sys.platform == "darwin":
        opts.ocr_options = OcrMacOptions(
            lang=["en-US"],
            recognition="accurate",
            framework="vision",
        )
    else:
        opts.ocr_options = EasyOcrOptions(lang=["en"])

    if profile == "fast":
        opts.images_scale = 1.0
        opts.do_formula_enrichment = False
        opts.do_code_enrichment = False
    elif profile == "balanced":
        opts.images_scale = 2.0
        opts.do_formula_enrichment = True
        opts.do_code_enrichment = True
    else:  # max_accuracy
        opts.images_scale = 3.0
        opts.do_formula_enrichment = True
        opts.do_code_enrichment = True
        opts.generate_picture_images = True
        opts.generate_page_images = True

    return opts


def transcribe(path: Path, *, profile: str = "balanced") -> tuple[str, list[Block], int, str]:
    """Run Docling on `path`. Returns (markdown, blocks, page_count, version_string)."""
    from importlib.metadata import version as _pkg_version

    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter, PdfFormatOption

    try:
        docling_version = _pkg_version("docling")
    except Exception:
        docling_version = "unknown"

    pipeline_options = _build_pipeline_options(profile)
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
        }
    )
    result = converter.convert(str(path))
    doc = result.document

    markdown = doc.export_to_markdown()

    blocks: list[Block] = []
    idx = 0
    try:
        iterator = doc.iterate_items()
    except AttributeError:
        iterator = []

    for item, _level in iterator:
        text = getattr(item, "text", None) or ""
        label = getattr(item, "label", None)
        label_str = getattr(label, "value", None) or (str(label) if label else None)
        btype = _label_to_type(label_str)
        page = _extract_page(item)
        bbox = _extract_bbox(item)

        if btype == BlockType.TABLE:
            try:
                text = item.export_to_markdown()
            except Exception:
                pass
        if not text and btype not in (BlockType.FIGURE, BlockType.TABLE):
            continue

        blocks.append(
            Block(
                id=_block_id(text, page, idx),
                type=btype,
                text=text,
                page=page,
                bbox=bbox,
                confidence=None,
                engine="docling",
                verify=VerifyStatus.UNVERIFIED,
                extra={"docling_label": label_str} if label_str else {},
            )
        )
        idx += 1

    page_count = len(getattr(doc, "pages", []) or []) or max((b.page for b in blocks), default=0)

    return markdown, blocks, page_count, docling_version
