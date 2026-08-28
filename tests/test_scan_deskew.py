"""Fine deskewing is gated, geometry-preserving, and limited to raster pages."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageDraw

from pdf2md.calibrate import scan_deskew_angle
from pdf2md.engines.base import EngineResult
from pdf2md.scan_deskew import deskew_scanned_pdf, restore_source_geometry
from pdf2md.schema import BBox, Block, BlockType, FigureRef, TableData
from pdf2md.table_verify import _read_tesseract, _table_layout


def _table_image(angle: float = 0.0) -> Image.Image:
    image = Image.new("L", (800, 600), 255)
    drawing = ImageDraw.Draw(image)
    for y in range(100, 520, 35):
        drawing.line((80, y, 720, y), fill=0, width=2)
        for x in range(120, 700, 100):
            drawing.text((x, y + 5), f"{x / 100:.2f}", fill=0)
    for x in (80, 250, 420, 590, 720):
        drawing.line((x, 90, x, 530), fill=0)
    return image.rotate(angle, expand=False, fillcolor=255) if angle else image


def test_scan_angle_refuses_small_and_search_boundary_corrections():
    upright = _table_image()

    assert scan_deskew_angle(np.asarray(upright)) == 0.0
    assert scan_deskew_angle(np.asarray(upright.rotate(-0.5, fillcolor=255))) == 0.0
    assert scan_deskew_angle(np.asarray(upright.rotate(-3.0, fillcolor=255))) == 0.0
    assert scan_deskew_angle(np.asarray(upright.rotate(-1.5, fillcolor=255))) == 1.5


def test_deskew_pdf_replaces_only_skewed_textless_pages(tmp_path):
    source_path = tmp_path / "scan.pdf"
    skewed = _table_image(-1.5).convert("RGB")
    upright = _table_image().convert("RGB")
    skewed.save(
        source_path,
        "PDF",
        save_all=True,
        append_images=[upright],
        resolution=150.0,
        creationDate="",
        modDate="",
    )

    with deskew_scanned_pdf(
        source_path, detection_dpi=150, output_dpi=150
    ) as prepared:
        temporary_path = prepared.path
        assert prepared.angles == {1: 1.5}
        assert temporary_path != source_path
        processed = pdfium.PdfDocument(str(temporary_path))
        source = pdfium.PdfDocument(str(source_path))
        try:
            assert len(processed) == 2
            angles = []
            for page in processed:
                gray = np.asarray(page.render(scale=1).to_pil().convert("L"))
                angles.append(scan_deskew_angle(gray))
            assert angles == [0.0, 0.0]
            assert np.array_equal(
                np.asarray(source[1].render(scale=1).to_pil()),
                np.asarray(processed[1].render(scale=1).to_pil()),
            )
        finally:
            source.close()
            processed.close()

    assert not temporary_path.exists()


def test_born_digital_pdf_bypasses_deskew():
    source_path = Path(__file__).parent / "fixtures" / "vector_plot.pdf"

    with deskew_scanned_pdf(source_path) as prepared:
        assert prepared.path == source_path
        assert prepared.angles == {}


def test_engine_boxes_return_to_original_page_coordinates():
    bbox = BBox(100.0, 400.0, 300.0, 200.0)
    block = Block("#/table/1", BlockType.TABLE, "", 1, bbox=bbox)
    table = TableData(block.id, 1, bbox, gfm="| A |\n|---|\n| 1 |")
    figure = FigureRef("#/figure/1", 1, bbox, caption_bbox=bbox)
    result = EngineResult(
        blocks=[block],
        tables=[table],
        figures=[figure],
        page_sizes={1: (400.0, 600.0)},
    )

    restore_source_geometry(result, {1: 2.0})

    restored = block.bbox
    assert restored is not None
    assert restored.x0 < 100.0 and restored.x1 > 300.0
    assert (restored.x0 + restored.x1) / 2 == 200.0
    assert (restored.y0 + restored.y1) / 2 == 300.0
    assert table.bbox == figure.bbox == figure.caption_bbox == restored
    assert block.extra["processing_deskew_degrees"] == 2.0


def test_table_reader_uses_deskewed_crop(tmp_path, monkeypatch):
    crop_path = tmp_path / "table.png"
    _table_image(-1.5).save(crop_path)
    rows = [["Dimer", "A", "B"], ["Li2", "1.0", "2.0"], ["N2", "3.0", "4.0"]]
    layout = _table_layout(rows, None)
    expected = {(1, 1): "1.0", (1, 2): "2.0", (2, 1): "3.0", (2, 2): "4.0"}

    def read_corrected(source_rows, reader_path, executable, source_layout):
        assert source_rows == rows
        assert source_layout == layout
        assert executable == "tesseract"
        gray = np.asarray(Image.open(reader_path).convert("L"))
        assert scan_deskew_angle(gray) == 0.0
        return expected, None

    monkeypatch.setattr("pdf2md.table_verify._run_tesseract", read_corrected)

    mapped, refusal, rotation, deskew = _read_tesseract(
        rows, crop_path, "tesseract", layout
    )

    assert mapped == expected
    assert refusal is None
    assert rotation == 0
    assert deskew == 1.5
