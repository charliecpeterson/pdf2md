"""Continuation geometry survives translation, coordinate normalization, and replay."""

import json
from contextlib import closing
from dataclasses import asdict
from types import SimpleNamespace

import pypdfium2 as pdfium

from pdf2md.engine_state import load_engine_state, write_engine_state
from pdf2md.engines.base import EngineResult, normalize_page_origin
from pdf2md.engines.docling import DoclingEngine
from pdf2md.schema import BBox, Block, BlockType, SourceSpan


def _translated():
    item = SimpleNamespace(
        self_ref="#/texts/0", label="text", text="First page. Second page.",
        prov=[
            SimpleNamespace(page_no=1, bbox=SimpleNamespace(l=10, t=90, r=40, b=70)),
            SimpleNamespace(page_no=2, bbox=SimpleNamespace(l=15, t=300, r=45, b=280)),
            SimpleNamespace(page_no=2, bbox=SimpleNamespace(l=60, t=300, r=90, b=280)),
        ],
    )
    doc = SimpleNamespace(iterate_items=lambda: [(item, 0)])
    engine = DoclingEngine.__new__(DoclingEngine)
    return engine._blocks(doc)[0]


def test_adapter_preserves_all_regions_in_engine_order_without_splitting_text():
    block = _translated()
    assert (block.id, block.text, block.page, block.bbox) == (
        "#/texts/0", "First page. Second page.", 1, BBox(10, 90, 40, 70))
    assert block.source_spans == [
        SourceSpan(1, BBox(10, 90, 40, 70)),
        SourceSpan(2, BBox(15, 300, 45, 280)),
        SourceSpan(2, BBox(60, 300, 90, 280)),
    ]
    assert asdict(block)["source_spans"][1] == {
        "page": 2, "bbox": {"x0": 15, "y0": 300, "x1": 45, "y1": 280}}


def test_legacy_and_other_engines_keep_primary_region_fallback():
    block = Block("p", BlockType.PARAGRAPH, "text", 3, engine="mineru")
    assert block.source_spans == []
    assert block.source_regions() == [SourceSpan(3, None)]


def test_saved_state_and_provenance_fallback_preserve_spans(tmp_path):
    block = _translated()
    result = EngineResult([block], [], [], {1: (600, 800), 2: (600, 800)})
    path = write_engine_state(tmp_path, "a" * 64, result)
    assert load_engine_state(tmp_path).blocks == [block]
    raw = json.loads(path.read_text())
    del raw["blocks"][0]["source_spans"]
    path.write_text(json.dumps(raw))
    old = load_engine_state(tmp_path).blocks[0]
    assert old.source_spans == []
    assert old.source_regions() == [SourceSpan(1, BBox(10, 90, 40, 70))]
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    (fallback / "provenance.json").write_text(json.dumps({"blocks": [asdict(block)]}))
    assert load_engine_state(fallback).blocks == [block]


def test_each_span_uses_its_own_page_origin_and_shifts_once(tmp_path):
    source = tmp_path / "origins.pdf"
    with closing(pdfium.PdfDocument.new()) as pdf:
        for x, y in ((10, 20), (30, 40)):
            page = pdf.new_page(600, 800)
            page.set_cropbox(x, y, 590, 790)
            page.close()
        pdf.save(source)
    block = _translated()
    result = EngineResult([block], [], [], {1: (580, 770), 2: (560, 750)})
    normalize_page_origin(result, source)
    assert block.bbox == BBox(20, 110, 50, 90)
    assert block.source_spans == [
        SourceSpan(1, BBox(20, 110, 50, 90)),
        SourceSpan(2, BBox(45, 340, 75, 320)),
        SourceSpan(2, BBox(90, 340, 120, 320)),
    ]
    write_engine_state(tmp_path, "a" * 64, result)
    assert load_engine_state(tmp_path).blocks[0].source_spans == block.source_spans
