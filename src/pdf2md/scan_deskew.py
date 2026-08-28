"""Conservatively deskew textless PDF pages before structured OCR.

Only pages with a strong projection-based angle are raster-replaced. Geometry from
the corrected OCR input is mapped back onto the original page for source crops.
"""

from __future__ import annotations

import math
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, NamedTuple

import numpy as np
import pypdfium2 as pdfium
from PIL import Image

from pdf2md.calibrate import scan_deskew_angle
from pdf2md.engines.base import EngineResult
from pdf2md.schema import BBox


class PreparedScan(NamedTuple):
    path: Path
    angles: dict[int, float]


def deskew_image(image: Image.Image) -> tuple[Image.Image, float]:
    """Return a corrected copy and the applied angle, or the original copy at zero."""
    angle = scan_deskew_angle(np.asarray(image.convert("L")))
    rgb = image.convert("RGB")
    if not angle:
        return rgb, 0.0
    return (
        rgb.rotate(
            angle,
            resample=Image.Resampling.BICUBIC,
            expand=False,
            fillcolor="white",
        ),
        angle,
    )


@contextmanager
def deskew_scanned_pdf(
    pdf_path: Path, *, detection_dpi: int = 150, output_dpi: int = 300
) -> Iterator[PreparedScan]:
    """Yield a temporary PDF with only confidently skewed textless pages replaced."""
    source = pdfium.PdfDocument(str(pdf_path))
    angles: dict[int, float] = {}
    try:
        for page_index in range(len(source)):
            page = source[page_index]
            if page.get_textpage().get_text_bounded().strip():
                continue
            preview = page.render(scale=detection_dpi / 72.0).to_pil().convert("L")
            angle = scan_deskew_angle(np.asarray(preview))
            if angle:
                angles[page_index + 1] = angle

        if not angles:
            yield PreparedScan(Path(pdf_path), {})
            return

        with tempfile.TemporaryDirectory(prefix="pdf2md-deskew-") as temp_dir:
            temp = Path(temp_dir)
            processed_path = temp / "deskewed.pdf"
            processed = pdfium.PdfDocument.new()
            replacements = []
            try:
                for page_index in range(len(source)):
                    page_number = page_index + 1
                    angle = angles.get(page_number)
                    if angle is None:
                        processed.import_pages(source, pages=[page_index])
                        continue
                    image = source[page_index].render(
                        scale=output_dpi / 72.0
                    ).to_pil().convert("RGB")
                    corrected = image.rotate(
                        angle,
                        resample=Image.Resampling.BICUBIC,
                        expand=False,
                        fillcolor="white",
                    )
                    page_path = temp / f"page-{page_number}.pdf"
                    corrected.save(
                        page_path,
                        "PDF",
                        resolution=float(output_dpi),
                        creationDate="",
                        modDate="",
                    )
                    replacement = pdfium.PdfDocument(str(page_path))
                    replacements.append(replacement)
                    processed.import_pages(replacement, pages=[0])
                processed.save(str(processed_path))
            finally:
                processed.close()
                for replacement in replacements:
                    replacement.close()
            yield PreparedScan(processed_path, angles)
    finally:
        source.close()


def _source_bbox(
    bbox: BBox | None, page_size: tuple[float, float], angle: float
) -> BBox | None:
    if bbox is None or not angle:
        return bbox
    width, height = page_size
    center_x, center_y = width / 2.0, height / 2.0
    radians = math.radians(angle)
    cosine, sine = math.cos(radians), math.sin(radians)
    top = height - max(bbox.y0, bbox.y1)
    bottom = height - min(bbox.y0, bbox.y1)
    corners = (
        (min(bbox.x0, bbox.x1), top),
        (max(bbox.x0, bbox.x1), top),
        (max(bbox.x0, bbox.x1), bottom),
        (min(bbox.x0, bbox.x1), bottom),
    )
    original = [
        (
            center_x + cosine * (x - center_x) - sine * (y - center_y),
            center_y + sine * (x - center_x) + cosine * (y - center_y),
        )
        for x, y in corners
    ]
    left = max(0.0, min(x for x, _ in original))
    right = min(width, max(x for x, _ in original))
    original_top = max(0.0, min(y for _, y in original))
    original_bottom = min(height, max(y for _, y in original))
    return BBox(left, height - original_top, right, height - original_bottom)


def restore_source_geometry(result: EngineResult, angles: dict[int, float]) -> None:
    """Map deskewed engine boxes back to the original page and record the transform."""
    if not angles:
        return
    for block in result.blocks:
        angle = angles.get(block.page)
        if angle:
            block.bbox = _source_bbox(block.bbox, result.page_sizes[block.page], angle)
            block.extra["processing_deskew_degrees"] = angle
    for table in result.tables:
        angle = angles.get(table.page)
        if angle:
            table.bbox = _source_bbox(table.bbox, result.page_sizes[table.page], angle)
    for figure in result.figures:
        angle = angles.get(figure.page)
        if angle:
            page_size = result.page_sizes[figure.page]
            figure.bbox = _source_bbox(figure.bbox, page_size, angle)
            figure.caption_bbox = _source_bbox(figure.caption_bbox, page_size, angle)
