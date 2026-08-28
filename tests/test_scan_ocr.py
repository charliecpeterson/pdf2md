"""Whole-page vision OCR retains text and reuses cached reads."""

from __future__ import annotations

from pathlib import Path

from pdf2md.config import Config
from pdf2md.scan_ocr import _vlm_ocr_pages
from pdf2md.schema import Block, BlockType


class _CountingDescriber:
    def __init__(self, text: str):
        self.text = text
        self.calls = 0
        self.last_truncated = False

    def model_for(self, kind: str) -> str:
        return "fake-ocr"

    def describe(self, image_path, kind, context="", **kwargs):
        self.calls += 1
        return self.text


def test_whole_page_ocr_reuses_document_cache(tmp_path):
    pdf = Path(__file__).parent / "fixtures" / "vector_plot.pdf"
    document_dir = tmp_path / "document"
    config = Config(force_ocr=True, page_image_dpi=72)
    describer = _CountingDescriber("# Recovered page\n\nExact text")
    blocks = [Block("#/old", BlockType.PARAGRAPH, "engine text", 1)]

    first = _vlm_ocr_pages(blocks, describer, pdf, config, document_dir)
    second = _vlm_ocr_pages(blocks, describer, pdf, config, document_dir)

    assert describer.calls == 1
    assert first[0].text == second[0].text == "# Recovered page\n\nExact text"
    assert first[0].extra["text_source"] == "vlm-page"
    assert (document_dir / "describe_cache.json").is_file()
