"""Citation formatting (APA, BibTeX) from doc metadata.

Stays separate from the MCP tool layer so it's testable in isolation and
reusable (e.g., a future export-to-Markdown pipeline).
"""

from __future__ import annotations

from typing import Any


def parse_authors(raw: str | None) -> list[tuple[str, str]]:
    """Parse 'Edward F. Valeev, Robert J. Harrison, and Adam Holmes' into
    [(family, initials), ...]. Tolerant of `&`, `and`, comma separators."""
    if not raw:
        return []
    cleaned = raw.replace(" and ", ", ").replace(" & ", ", ")
    out: list[tuple[str, str]] = []
    for piece in cleaned.split(","):
        name = piece.strip()
        if not name:
            continue
        toks = name.split()
        if not toks:
            continue
        family = toks[-1]
        givens = toks[:-1]
        parts = []
        for g in givens:
            if not g:
                continue
            if len(g) <= 2 and g.endswith("."):
                parts.append(g)
            elif g[0].isupper():
                parts.append(f"{g[0]}.")
            else:
                parts.append(g)
        initials = " ".join(parts)
        out.append((family, initials))
    return out


def format_apa(doc: dict[str, Any]) -> str:
    """Produce a 7th-edition-flavored APA citation string."""
    authors = parse_authors(doc.get("authors"))
    parts: list[str] = []
    if authors:
        formatted = [
            f"{family}, {initials}".rstrip(", ").rstrip()
            for family, initials in authors
        ]

        def _ensure_terminal_period(s: str) -> str:
            return s if s.endswith(".") else s + "."

        if len(formatted) == 1:
            parts.append(_ensure_terminal_period(formatted[0]))
        else:
            parts.append(
                ", ".join(formatted[:-1])
                + ", & "
                + _ensure_terminal_period(formatted[-1])
            )
    if doc.get("year"):
        parts.append(f"({doc['year']}).")
    if doc.get("title"):
        parts.append(doc["title"] + ".")
    if doc.get("journal"):
        jpart = doc["journal"]
        if doc.get("volume"):
            jpart += f", {doc['volume']}"
        if doc.get("pages"):
            jpart += f", {doc['pages']}"
        parts.append(jpart + ".")
    if doc.get("doi"):
        parts.append(f"https://doi.org/{doc['doi']}")
    return " ".join(parts)


def format_bibtex(doc: dict[str, Any]) -> str:
    """Produce a BibTeX @article entry from doc metadata."""
    authors = parse_authors(doc.get("authors"))
    first_family = authors[0][0].lower() if authors else "doc"
    key_base = "".join(c for c in first_family if c.isalnum()) or "doc"
    key = f"{key_base}{doc.get('year') or 'nd'}"
    lines = [f"@article{{{key},"]
    if doc.get("title"):
        lines.append(f"  title = {{{doc['title']}}},")
    if authors:
        author_str = " and ".join(
            f"{family}, {initials}".rstrip(", ") for family, initials in authors
        )
        lines.append(f"  author = {{{author_str}}},")
    if doc.get("journal"):
        lines.append(f"  journal = {{{doc['journal']}}},")
    if doc.get("year"):
        lines.append(f"  year = {{{doc['year']}}},")
    if doc.get("volume"):
        lines.append(f"  volume = {{{doc['volume']}}},")
    if doc.get("pages"):
        lines.append(f"  pages = {{{doc['pages']}}},")
    if doc.get("doi"):
        lines.append(f"  doi = {{{doc['doi']}}},")
    lines.append("}")
    return "\n".join(lines)
