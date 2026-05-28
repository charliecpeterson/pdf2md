from __future__ import annotations

import re
from typing import Any

_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_AUTHOR_LIKE = re.compile(
    r"\b[A-Z][a-z]+(?:\s[A-Z]\.)*\s[A-Z][a-z]+\b"
)
_COPYRIGHT = re.compile(r"©\s*(\d{4})", re.IGNORECASE)
_PUBLISHED = re.compile(r"\b(?:Published|Copyright)\b", re.IGNORECASE)


def extract_metadata(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """Heuristic extraction of title/authors/year from a doc's first ~30 blocks.

    Designed to be defensive: returns whatever fields it could identify with
    reasonable confidence. The caller is expected to allow user overrides via
    set_metadata().
    """
    if not blocks:
        return {}

    front = blocks[:40]

    title = _extract_title(front)
    authors = _extract_authors(front, title)
    year = _extract_year(front)

    out: dict[str, Any] = {}
    if title:
        out["title"] = title
    if authors:
        out["authors"] = authors
    if year:
        out["year"] = year
    return out


def _extract_title(front: list[dict[str, Any]]) -> str | None:
    """First substantial heading on page 1, or first long text block on page 1."""
    candidates: list[str] = []
    for b in front:
        if b.get("page", 0) > 2:
            break
        text = (b.get("text") or "").strip()
        if not text:
            continue
        if b.get("type") == "heading" and 6 <= len(text) <= 200:
            candidates.append(text)
    if candidates:
        # Prefer the longest non-trivial heading on page 1
        candidates.sort(key=len, reverse=True)
        return candidates[0]
    # Fallback: first paragraph on page 1 that looks like a title (short, no period mid-line)
    for b in front:
        if b.get("page", 0) > 1:
            break
        text = (b.get("text") or "").strip()
        if 8 <= len(text) <= 120 and "." not in text[: len(text) - 1]:
            return text
    return None


def _extract_authors(front: list[dict[str, Any]], title: str | None) -> str | None:
    """Look for 'X Y' or 'X Y, A B' name-like patterns on the first 1-2 pages."""
    seen: list[str] = []
    for b in front:
        if b.get("page", 0) > 2:
            break
        text = (b.get("text") or "").strip()
        if not text or text == title:
            continue
        if _PUBLISHED.search(text):
            continue
        names = _AUTHOR_LIKE.findall(text)
        if names and 1 <= len(names) <= 8 and len(text) < 200:
            seen.extend(names)
            if len(seen) >= 2 or len(names) >= 2:
                break
    if not seen:
        return None
    deduped: list[str] = []
    for n in seen:
        if n not in deduped:
            deduped.append(n)
    return ", ".join(deduped[:6])


def _extract_year(front: list[dict[str, Any]]) -> int | None:
    for b in front:
        if b.get("page", 0) > 5:
            break
        text = (b.get("text") or "").strip()
        m = _COPYRIGHT.search(text)
        if m:
            return int(m.group(1))
        if _PUBLISHED.search(text):
            ym = _YEAR.search(text)
            if ym:
                return int(ym.group(0))
    # Last resort: first 4-digit year that looks reasonable
    for b in front:
        if b.get("page", 0) > 3:
            break
        text = (b.get("text") or "").strip()
        ym = _YEAR.search(text)
        if ym:
            y = int(ym.group(0))
            if 1900 <= y <= 2100:
                return y
    return None
