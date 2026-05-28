from __future__ import annotations

from pathlib import Path

from docsmcp.store.schema import PageClass, PageTriage

_TEXT_THRESHOLD = 100
_IMAGE_THRESHOLD = 1


def _classify(char_count: int, image_count: int) -> PageClass:
    if char_count >= _TEXT_THRESHOLD and image_count <= _IMAGE_THRESHOLD:
        return PageClass.BORN_DIGITAL
    if char_count >= _TEXT_THRESHOLD and image_count > _IMAGE_THRESHOLD:
        return PageClass.MIXED
    if char_count < _TEXT_THRESHOLD and image_count >= 1:
        return PageClass.SCANNED
    return PageClass.EMPTY


def triage_pdf(path: Path) -> list[PageTriage]:
    import fitz

    pages: list[PageTriage] = []
    with fitz.open(str(path)) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text() or ""
            images = page.get_images(full=False) or []
            rect = page.rect
            pages.append(
                PageTriage(
                    page=i,
                    char_count=len(text.strip()),
                    image_count=len(images),
                    width=float(rect.width),
                    height=float(rect.height),
                    classification=_classify(len(text.strip()), len(images)),
                )
            )
    return pages


def summarize(triage: list[PageTriage]) -> dict[str, int]:
    counts: dict[str, int] = {c.value: 0 for c in PageClass}
    for p in triage:
        counts[p.classification.value] += 1
    return counts
