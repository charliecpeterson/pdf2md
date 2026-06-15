"""Lightweight bibliographic metadata: the PDF's embedded fields first, then a
first-page heuristic to fill gaps. No network (CrossRef/GROBID are deferred)."""

from __future__ import annotations

import re

import pypdfium2 as pdfium

from pdf2md.schema import Block, BlockType

_DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_AUTHOR_SPLIT = re.compile(r"\s*(?:;|,| and )\s*")


def extract_metadata(pdf_path, blocks: list[Block]) -> dict:
    embedded = _embedded(pdf_path)
    page1 = [b for b in blocks if b.page == 1]

    title = (embedded.get("Title") or "").strip()
    if not title:
        head = next(
            (b for b in page1 if b.type == BlockType.HEADING and b.text.strip()), None
        )
        title = head.text.strip() if head else ""

    author = (embedded.get("Author") or "").strip()
    authors = [a for a in _AUTHOR_SPLIT.split(author) if a] if author else []

    text1 = "\n".join(b.text for b in page1)
    doi_match = _DOI.search(text1)
    year_match = _YEAR.search(text1)

    return {
        "title": title or None,
        "authors": authors or None,
        "year": year_match.group(0) if year_match else None,
        "doi": doi_match.group(0) if doi_match else None,
    }


def _embedded(pdf_path) -> dict:
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        return pdf.get_metadata_dict() or {}
    except Exception:  # noqa: BLE001 - missing/garbled metadata is normal
        return {}
    finally:
        pdf.close()
