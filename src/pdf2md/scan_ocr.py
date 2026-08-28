"""Whole-page vision OCR for scanned documents.

It preserves engine-detected figures, exposes failed or truncated reads, and
reuses the document-level inference cache across conversion versions.
"""

from __future__ import annotations

import tempfile
from collections import defaultdict
from pathlib import Path

import pypdfium2 as pdfium

from pdf2md.config import Config
from pdf2md.describe import Describer, clean_vlm_text, vision_cache_key
from pdf2md.logging import get_logger
from pdf2md.render import CropRenderer
from pdf2md.schema import Block, BlockType
from pdf2md.vision_cache import CacheStats, load_vision_cache, write_vision_cache

log = get_logger("scan_ocr")


def _pages_to_transcribe(pdf_path: Path, force_ocr: bool) -> set[int]:
    """The 1-based pages to hand the page-transcription model: every page under --force-ocr, else
    only the scanned ones (no embedded text layer). Enumerated straight from the PDF, not from
    Docling's blocks: with OCR skipped a scanned text page has no blocks, so it would otherwise be
    invisible. A born-digital page keeps its text layer and is left to Docling."""
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        if force_ocr:
            return set(range(1, len(pdf) + 1))
        return {i + 1 for i in range(len(pdf))
                if not pdf[i].get_textpage().get_text_bounded().strip()}
    finally:
        pdf.close()


def _vlm_ocr_pages(blocks, describer: Describer, pdf_path: Path, config: Config,
                   doc_dir: Path, *, cache_stats: CacheStats | None = None):
    """Transcribe each scanned page whole with the vision model. Scanned pages are
    enumerated from the PDF, so pages without engine blocks remain visible. Each page
    becomes one transcription block followed by any detected figures."""
    targets = _pages_to_transcribe(pdf_path, config.force_ocr)
    if not targets:
        return blocks
    cache = load_vision_cache(doc_dir, cache_stats)
    transcript: dict[int, str] = {}
    cap_truncated: set[int] = set()
    loop_truncated: set[int] = set()
    with tempfile.TemporaryDirectory() as td, \
            CropRenderer(pdf_path, dpi=config.page_image_dpi) as cr:
        tmp = Path(td)
        for page in sorted(targets):
            img = tmp / f"page_{page}.png"
            try:
                cr.full_page(page, img)
            except Exception as exc:  # noqa: BLE001 - page-level isolate-and-skip
                log.warning("page render failed for page %d: %s", page, exc)
                continue
            key = vision_cache_key(
                img, describer, "page", max_tokens=config.vlm_max_tokens,
                endpoint=config.vlm_base_url,
            )
            text = cache.get(key)
            if text is None:
                text = describer.describe(img, "page", max_tokens=config.vlm_max_tokens)
                if getattr(describer, "last_truncated", False):
                    cap_truncated.add(page)
                if text:
                    cache[key] = text
            cleaned, looped = clean_vlm_text(text) if text else ("", False)
            if cleaned:
                transcript[page] = cleaned
                if looped:
                    loop_truncated.add(page)
                log.info("vlm page transcription for page %d: %d chars", page, len(cleaned))
    write_vision_cache(doc_dir, cache)
    return _apply_page_transcripts(blocks, targets, transcript, cap_truncated, loop_truncated)


def _page_block(page: int, text: str, *, failed: bool = False) -> Block:
    """The single block a transcribed page collapses to."""
    b = Block(id=f"#/page/{page}", type=BlockType.PARAGRAPH, text=text, page=page)
    b.extra = {"ocr": True, "text_source": "vlm-page-failed" if failed else "vlm-page"}
    return b


def _apply_page_transcripts(blocks, targets: set[int], transcript: dict[int, str],
                            cap_truncated: set[int] = frozenset(),
                            loop_truncated: set[int] = frozenset()):
    """Rebuild target pages from whole-page text plus detected figure blocks."""
    by_page: dict[int, list] = defaultdict(list)
    for b in blocks:
        by_page[b.page].append(b)
    out: list = []
    for page in sorted(set(by_page) | targets):
        if page not in targets:
            out.extend(by_page[page])
            continue
        if page in transcript:
            blk = _page_block(page, transcript[page])
            if page in cap_truncated:
                blk.extra["ocr_cap_truncated"] = True
            if page in loop_truncated:
                blk.extra["ocr_loop_truncated"] = True
            out.append(blk)
        else:
            out.append(_page_block(page, f"> **[pdf2md: page {page} not transcribed — see the page "
                                         "scan]**", failed=True))
        out.extend(b for b in by_page[page] if b.type is BlockType.FIGURE)
    return out
