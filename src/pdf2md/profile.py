"""Build a DocumentProfile: what the conversion contains and how much to trust it.

One aggregate over the converted document, computed after coverage. It feeds three
consumers — profile.json (machine-readable, for an AI), README.md (a human run
summary), and the accuracy harness (which checks these signals against ground truth)
— so the "is this conversion good?" judgement is computed once, not re-derived.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from pdf2md.schema import BlockType, Document, DocumentProfile

# Prose-bearing types held to the legibility bar (mirrors enrich/emit/qa).
_PROSE = {"paragraph", "heading", "list", "caption", "footnote", "other"}
_GRADES = ("high", "medium", "low")  # ordered best -> worst


def _downgrade(current: str, to: str) -> str:
    return to if _GRADES.index(to) > _GRADES.index(current) else current


def build_profile(doc: Document) -> DocumentProfile:
    blocks = doc.blocks
    by_type = Counter(b.type.value for b in blocks)
    eqs = [b for b in blocks if b.type is BlockType.EQUATION]
    image_backed = sum(1 for b in eqs if b.extra.get("crop_path"))
    ocr_pages = len({b.page for b in blocks if b.extra.get("ocr")})

    prose = [b for b in blocks if b.type.value in _PROSE and b.text.strip()]
    illegible = doc.coverage.illegible if doc.coverage else 0
    legibility = (len(prose) - illegible) / len(prose) if prose else 1.0
    lossless = doc.coverage.lossless if doc.coverage else True

    ocr_by_vlm = any(b.extra.get("text_source") == "vlm-ocr" for b in blocks)
    grade, reasons = _confidence(lossless, illegible, ocr_pages, doc.page_count,
                                 len(eqs), image_backed, ocr_by_vlm)
    return DocumentProfile(
        pages=doc.page_count,
        blocks=len(blocks),
        by_type=dict(by_type),
        figures=len(doc.figures),
        tables=len(doc.tables),
        equations=len(eqs),
        equations_image_backed=image_backed,
        code_blocks=by_type.get("code", 0),
        illegible_blocks=illegible,
        ocr_pages=ocr_pages,
        lossless=lossless,
        prose_legibility=round(legibility, 4),
        confidence=grade,
        confidence_reasons=reasons,
    )


def _confidence(lossless, illegible, ocr_pages, pages, equations, image_backed, ocr_by_vlm=False):
    grade = "high"
    reasons: list[str] = []
    by = "OCR by a vision model" if ocr_by_vlm else "OCR text"
    if not lossless:
        grade = _downgrade(grade, "low")
        reasons.append("not lossless — content was dropped without a marker")
    if illegible:
        grade = _downgrade(grade, "low" if illegible > 5 else "medium")
        reasons.append(f"{illegible} illegible block(s) — broken font not recovered")
    if pages and ocr_pages / pages > 0.5:
        grade = _downgrade(grade, "medium")
        reasons.append(f"{ocr_pages}/{pages} pages scanned — {by}, verify against the images")
    elif ocr_pages:
        reasons.append(f"{ocr_pages} scanned page(s) — {by}, not a born-digital layer")
    if equations and image_backed:
        reasons.append(f"{image_backed}/{equations} equations image-backed — LaTeX unverified, "
                       "the crop is authoritative")
    if not reasons:
        reasons.append("clean born-digital extraction, nothing flagged")
    return grade, reasons


def write_profile(version_dir: Path, doc: Document, profile: DocumentProfile,
                  md_files: list[Path]) -> Path:
    """profile.json: the profile plus the output file list and a pointer to the
    contents tree — the machine-readable 'what is this and how do I read it'."""
    names = [p.name for p in md_files]
    data = {
        "doc_id": doc.doc_id[:16],
        "source": Path(doc.source_path).name,
        **asdict(profile),
        "files": names,
        "contents": "index.md" if "index.md" in names else (names[0] if names else None),
    }
    path = version_dir / "profile.json"
    path.write_text(json.dumps(data, indent=2))
    return path


def write_readme(version_dir: Path, doc: Document, meta: dict, profile: DocumentProfile,
                 md_files: list[Path]) -> Path:
    """README.md: a human run summary — what the doc is, what's in it, how much to
    trust it, and where to start. Renders first when the output folder is opened."""
    names = [p.name for p in md_files]
    contents = "index.md" if "index.md" in names else (names[0] if names else "the markdown files")
    p = profile
    inv = ", ".join(f"{n} {label}" for n, label in [
        (p.equations, f"equations ({p.equations_image_backed} image-backed)" if p.equations else ""),
        (p.tables, "tables"), (p.figures, "figures"), (p.code_blocks, "code blocks"),
    ] if n) or "text only"

    lines = [
        f"# {meta.get('title') or Path(doc.source_path).stem} — conversion summary",
        "",
        f"`{Path(doc.source_path).name}` · {p.pages} pages · `doc_id {doc.doc_id[:16]}` · "
        f"converted by pdf2md.",
        "",
        f"## Confidence: {p.confidence}",
        "",
        *[f"- {r}" for r in p.confidence_reasons],
        "",
        "## Contents",
        "",
        f"{p.blocks} blocks across {p.pages} pages: {inv}."
        + (f" {p.illegible_blocks} block(s) remained illegible." if p.illegible_blocks else "")
        + (f" {p.ocr_pages} page(s) were scanned (OCR text)." if p.ocr_pages else ""),
        "",
        "## Where to start",
        "",
        f"Open [`{contents}`]({contents})"
        + (" for the linked contents tree." if contents == "index.md" else " for the document."),
        "Each file's YAML front-matter carries the bibliographic metadata; "
        "`profile.json` has this summary in machine-readable form.",
        "Image-backed equations and cropped figures keep the image as the authoritative "
        "source; any `[pdf2md: ...]` marker flags something to verify against it.",
        "",
    ]
    path = version_dir / "README.md"
    path.write_text("\n".join(lines))
    return path
