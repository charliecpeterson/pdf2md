"""Crop bbox regions to PNG via pypdfium2 (permissive; replaces the old PyMuPDF
path). pypdfium2 has no clip-render, so we render the page once, cache it, and
crop in pixel space. Bboxes are PDF points with a bottom-left origin (y0 > y1),
so the Y axis is flipped into the image's top-left pixel space.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pypdfium2 as pdfium

from pdf2md.logging import get_logger
from pdf2md.schema import BBox

log = get_logger("render")

_MIN_SIDE_PTS = 4.0
_DPI_CEILING = 600  # past this the file grows but the model downsamples anyway


def dpi_for_region(bbox: BBox, target_px: int, floor: int, ceiling: int = _DPI_CEILING) -> int:
    """DPI to render `bbox` at so its long side lands near `target_px`, clamped to
    [floor, ceiling]. Small dense regions climb toward the ceiling (sharp glyphs);
    large ones stay at the floor (no point — the model downsamples them)."""
    long_side_in = max(abs(bbox.x1 - bbox.x0), abs(bbox.y0 - bbox.y1)) / 72.0
    if long_side_in <= 0:
        return floor
    return max(floor, min(ceiling, round(target_px / long_side_in)))


def svg_crop(pdf_path: Path, page: int, bbox: BBox, out_path: Path,
             padding_pts: float = 6.0, timeout: float = 30.0) -> bool:
    """Export the bbox region as SVG via pdftocairo (poppler) — the lossless *text* form
    of a born-digital vector figure: the drawn geometry and embedded text survive as
    elements a reader can parse, unlike a PNG crop. Opportunistic: returns False (logged)
    when pdftocairo isn't installed or fails, and the PNG stays the record.

    pdftocairo's -x/-y/-W/-H crop flags are silently ignored for SVG output (despite the
    man page; verified on poppler 25.07), so the region is cropped upstream instead: the
    page is copied into a temp one-page PDF whose CropBox is the padded bbox, and that is
    what pdftocairo renders."""
    exe = shutil.which("pdftocairo")
    if exe is None:
        log.info("pdftocairo not found (brew install poppler) — skipping SVG export")
        return False
    x0, x1 = sorted((bbox.x0, bbox.x1))
    y0, y1 = sorted((bbox.y0, bbox.y1))
    if x1 - x0 < _MIN_SIDE_PTS or y1 - y0 < _MIN_SIDE_PTS:
        return False  # malformed bbox: the PNG path already falls back to the full page
    out_path.parent.mkdir(parents=True, exist_ok=True)
    src = pdfium.PdfDocument(str(pdf_path))
    one = pdfium.PdfDocument.new()
    with tempfile.TemporaryDirectory() as td:
        tmp_pdf = Path(td) / "region.pdf"
        try:
            one.import_pages(src, pages=[page - 1])
            pg = one[0]
            w, h = pg.get_size()
            pg.set_cropbox(max(0.0, x0 - padding_pts), max(0.0, y0 - padding_pts),
                           min(w, x1 + padding_pts), min(h, y1 + padding_pts))
            one.save(str(tmp_pdf))
        finally:
            one.close()
            src.close()
        cmd = [exe, "-svg", str(tmp_pdf), str(out_path)]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("svg export failed for page %d: %s", page, exc)
            return False
    if res.returncode != 0 or not out_path.exists():
        log.warning("svg export failed for page %d: %s", page,
                    res.stderr.decode(errors="replace").strip())
        return False
    # An embedded-raster figure (a photo, a journal's pre-rasterized plot) exports as an
    # SVG that just wraps the bitmap as base64 — bigger than the PNG and with no text
    # value, so the "lossless text form" claim fails. Keep only genuinely vector output.
    if "<image" in out_path.read_text(errors="replace"):
        out_path.unlink()
        log.info("figure on page %d is an embedded raster — SVG export skipped", page)
        return False
    return True


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

    def crop(self, page: int, bbox: BBox, out_path: Path, *, dpi: int | None = None) -> None:
        """Crop `bbox` on `page` (1-based) to `out_path`. Falls back to the full
        page if the bbox is malformed, so a visual is never silently lost.

        With `dpi` set, the region is rasterized on its own at that resolution via
        pdfium's crop-before-render, instead of cut from the cached page raster. A
        220-dpi page-crop caps a small region's detail at whatever 220 dpi sampled;
        re-rendering just the region at, say, 600 recovers the sub/superscripts and
        thin strokes an OCR/VLM otherwise misreads — for born-digital pages. (On a
        scanned page the ceiling is the embedded image's own resolution.)"""
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
            img = full  # malformed bbox: keep the whole page rather than lose the visual
        elif dpi is not None:
            s = dpi / 72.0  # render only the region, so 600 dpi costs region area, not page area
            img = self._pdf[page - 1].render(
                scale=s, crop=(left, h - bottom, w - right, top)
            ).to_pil()
        else:
            s = self._scale
            img = full.crop((int(left * s), int(top * s), int(right * s), int(bottom * s)))

        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path)
