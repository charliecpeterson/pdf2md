"""Read the embedded PDF table of contents via pypdfium2. This is the preferred
section source-of-truth (more reliable than detected headings) when a PDF ships
one; many born-digital papers don't, so callers fall back to the heading outline.
"""

from __future__ import annotations

import pypdfium2 as pdfium

from pdf2md.logging import get_logger

log = get_logger("bookmarks")


def read_bookmarks(pdf_path) -> list[tuple[str, int, int]] | None:
    """Return [(title, page_index_0based, level), ...] in document order, or None
    if the PDF has no usable outline."""
    pdf = pdfium.PdfDocument(str(pdf_path))
    out: list[tuple[str, int, int]] = []
    try:
        for item in pdf.get_toc():
            page_index = item.page_index
            if page_index is None:
                continue
            title = (item.title or "").strip()
            if title:
                out.append((title, page_index, item.level))
    except Exception as exc:  # noqa: BLE001 - a broken outline shouldn't abort conversion
        log.warning("could not read bookmarks: %s", exc)
        return None
    finally:
        pdf.close()
    return out or None
