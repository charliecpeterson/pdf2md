from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any

_HTML_TAG = re.compile(r"<[^>]+>")


def _strip_html(s: str | None) -> str | None:
    if not s:
        return s
    return _HTML_TAG.sub("", s).strip()

# DOI pattern per CrossRef recommendation; matches the vast majority of real DOIs.
_DOI = re.compile(
    r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE
)
_DOI_PREFIX = re.compile(r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*|DOI:\s*)", re.IGNORECASE)

_USER_AGENT = "docsmcp/0.1 (mailto:docsmcp@example.local)"
_TIMEOUT_S = 5.0


def extract_doi(text: str) -> str | None:
    """Best-effort DOI extraction from a text blob (e.g., first few blocks of a paper)."""
    if not text:
        return None
    cleaned = _DOI_PREFIX.sub("", text)
    m = _DOI.search(cleaned)
    if not m:
        return None
    doi = m.group(0).rstrip(".,;)")
    return doi.lower()


_DOI_BLOCKLIST_HINTS = (
    "ACS Publications website at DOI",  # ACS Supporting Info references
)


def _looks_like_paper_doi(block_text: str, doi: str) -> bool:
    """Cheap filter: if the surrounding text is obviously about a reference or SI,
    treat it as lower priority (we'll still take it if nothing else is found)."""
    return not any(h.lower() in block_text.lower() for h in _DOI_BLOCKLIST_HINTS)


def extract_doi_from_blocks(blocks: list[dict[str, Any]]) -> str | None:
    """Walk all blocks looking for a DOI. Prefer DOIs that look like the paper's
    own (not an SI/reference pointer); fall back to first DOI found anywhere."""
    fallback: str | None = None
    for b in blocks:
        text = (b.get("text") or "")
        doi = extract_doi(text)
        if not doi:
            continue
        if _looks_like_paper_doi(text, doi):
            return doi
        if fallback is None:
            fallback = doi
    return fallback


def _format_authors(crossref_authors: list[dict[str, Any]] | None) -> str | None:
    if not crossref_authors:
        return None
    parts = []
    for a in crossref_authors:
        given = (a.get("given") or "").strip()
        family = (a.get("family") or "").strip()
        if family and given:
            parts.append(f"{given} {family}")
        elif family:
            parts.append(family)
        elif given:
            parts.append(given)
    return ", ".join(parts) if parts else None


def _format_year(crossref_msg: dict[str, Any]) -> int | None:
    for key in ("published-print", "published-online", "issued", "created"):
        v = crossref_msg.get(key)
        if not v:
            continue
        date_parts = v.get("date-parts") or []
        if date_parts and date_parts[0]:
            y = date_parts[0][0]
            if isinstance(y, int) and 1900 <= y <= 2100:
                return y
    return None


def lookup_crossref(doi: str) -> dict[str, Any] | None:
    """Fetch metadata for a DOI via api.crossref.org. Returns dict with title/authors/year/journal/doi.

    Network-bound. Returns None on any failure (no exceptions raised).
    """
    if not doi:
        return None
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    msg = payload.get("message")
    if not msg:
        return None
    titles = msg.get("title") or []
    title = _strip_html(titles[0] if titles else None)
    pages = msg.get("page")
    volume = msg.get("volume")
    return {
        "doi": doi,
        "title": title,
        "authors": _format_authors(msg.get("author")),
        "year": _format_year(msg),
        "container": _strip_html((msg.get("container-title") or [None])[0]),
        "publisher": msg.get("publisher"),
        "type": msg.get("type"),
        "journal": _strip_html((msg.get("container-title") or [None])[0]),
        "volume": str(volume) if volume else None,
        "pages": str(pages) if pages else None,
    }


def enrich_metadata_from_blocks(blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """If a DOI is found in the doc, return authoritative CrossRef metadata. None on miss."""
    doi = extract_doi_from_blocks(blocks)
    if not doi:
        return None
    md = lookup_crossref(doi)
    if not md:
        return None
    out: dict[str, Any] = {"doi": doi}
    for k in ("title", "authors", "year", "journal", "volume", "pages"):
        if md.get(k):
            out[k] = md[k]
    return out
