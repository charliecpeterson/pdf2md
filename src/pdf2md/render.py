"""Crop bbox regions to PNG via pypdfium2 (permissive; replaces the old PyMuPDF
path). pypdfium2 has no clip-render, so we render the page once, cache it, and
crop in pixel space. Bboxes are PDF points with a bottom-left origin (y0 > y1),
so the Y axis is flipped into the image's top-left pixel space.
"""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium

from pdf2md.schema import BBox

_MIN_SIDE_PTS = 4.0


class CropRenderer:
    def __init__(self, pdf_path: Path, *, dpi: int = 220, padding_pts: float = 6.0) -> None:
        self._pdf = pdfium.PdfDocument(str(pdf_path))
        self._scale = dpi / 72.0
        self._padding = padding_pts
        self._page_cache: dict[int, object] = {}

    def close(self) -> None:
        self._pdf.close()

    def __enter__(self) -> "CropRenderer":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _page_image(self, page: int):
        if page not in self._page_cache:
            pg = self._pdf[page - 1]
            self._page_cache[page] = (pg.get_size(), pg.render(scale=self._scale).to_pil())
        return self._page_cache[page]

    def full_page(self, page: int, out_path: Path) -> None:
        """Render the whole page (1-based) to `out_path` — the verification raster for a
        scanned page, where the OCR text isn't authoritative and the image is."""
        _, full = self._page_image(page)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        full.save(out_path)

    def crop(self, page: int, bbox: BBox, out_path: Path) -> None:
        """Crop `bbox` on `page` (1-based) to `out_path`. Falls back to the full
        page if the bbox is malformed, so a visual is never silently lost."""
        (w, h), full = self._page_image(page)

        if bbox.y0 > bbox.y1:  # bottom-left origin: flip into top-left space
            top, bottom = h - bbox.y0, h - bbox.y1
        else:
            top, bottom = bbox.y0, bbox.y1
        left = max(0.0, min(bbox.x0, bbox.x1) - self._padding)
        right = min(w, max(bbox.x0, bbox.x1) + self._padding)
        top = max(0.0, top - self._padding)
        bottom = min(h, bottom + self._padding)

        if right - left < _MIN_SIDE_PTS or bottom - top < _MIN_SIDE_PTS:
            img = full
        else:
            s = self._scale
            img = full.crop((int(left * s), int(top * s), int(right * s), int(bottom * s)))

        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path)
