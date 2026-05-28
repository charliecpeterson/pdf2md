from __future__ import annotations

import hashlib
from pathlib import Path

from docsmcp.store.cache import doc_dir as get_doc_dir, latest_version


def _pages_dir(doc_id: str) -> Path:
    v = latest_version(get_doc_dir(doc_id)) or 1
    return get_doc_dir(doc_id) / f"v{v}" / "pages"


def render_page(
    source_pdf: Path,
    doc_id: str,
    page: int,
    *,
    dpi: int = 200,
    force: bool = False,
) -> Path:
    """Render one page of a PDF to PNG, cached under out/{doc}/v{n}/pages/."""
    import fitz

    out_dir = _pages_dir(doc_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"p{page:04d}@{dpi}dpi.png"
    if out_path.exists() and not force:
        return out_path

    with fitz.open(str(source_pdf)) as pdf:
        if not (1 <= page <= pdf.page_count):
            raise ValueError(f"page {page} out of range (1..{pdf.page_count})")
        pix = pdf[page - 1].get_pixmap(dpi=dpi)
        pix.save(str(out_path))
    return out_path


def render_block_crop(
    source_pdf: Path,
    doc_id: str,
    page: int,
    bbox: dict,
    *,
    block_id: str = "",
    dpi: int = 220,
    padding_pts: float = 6.0,
    force: bool = False,
) -> Path:
    """Crop the bbox region from `page` and save as a PNG."""
    import fitz

    key = block_id or hashlib.sha1(
        f"{page}:{bbox.get('x0')},{bbox.get('y0')},{bbox.get('x1')},{bbox.get('y1')}".encode()
    ).hexdigest()[:12]

    out_dir = _pages_dir(doc_id) / "crops"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"p{page:04d}_{key}@{dpi}dpi.png"
    if out_path.exists() and not force:
        return out_path

    with fitz.open(str(source_pdf)) as pdf:
        if not (1 <= page <= pdf.page_count):
            raise ValueError(f"page {page} out of range (1..{pdf.page_count})")
        pg = pdf[page - 1]
        h = pg.rect.height
        x0 = float(bbox["x0"])
        x1 = float(bbox["x1"])
        y0 = float(bbox["y0"])
        y1 = float(bbox["y1"])

        # Docling stores (t, b) where t > b in PDF coords (bottom-left origin).
        # PyMuPDF Rect expects (top, bottom) in top-left origin.
        # When y0 > y1, treat them as docling PDF coords and flip the Y axis.
        if y0 > y1:
            top = h - y0
            bottom = h - y1
        else:
            top = y0
            bottom = y1

        rect = fitz.Rect(
            max(0.0, x0 - padding_pts),
            max(0.0, top - padding_pts),
            min(pg.rect.width, x1 + padding_pts),
            min(pg.rect.height, bottom + padding_pts),
        )
        if rect.is_empty or rect.width < 4 or rect.height < 4:
            # Fall back to full page if bbox is malformed.
            pix = pg.get_pixmap(dpi=dpi)
        else:
            pix = pg.get_pixmap(dpi=dpi, clip=rect)
        pix.save(str(out_path))
    return out_path
