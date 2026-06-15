"""Heading classification helpers for the section-tree builder."""

from __future__ import annotations

import re

from pdf2md.schema import Block, SectionKind

# Leading section number: "3", "3.5", "3.5.1" → depth = dotted-component count.
_NUMBERING = re.compile(r"^\s*(\d+(?:\.\d+)*)\s")


def heading_depth(block: Block) -> int:
    """1-based heading depth. Docling's own heading level is unreliable (it tends
    to flatten everything to 1), so prefer section numbering when the title has
    it, and fall back to Docling's level, then 1."""
    m = _NUMBERING.match(block.text or "")
    if m:
        return min(m.group(1).count(".") + 1, 6)
    level = block.extra.get("level")
    if isinstance(level, int) and level >= 1:
        return min(level, 6)
    return 1


def section_kind(title: str, depth: int) -> SectionKind:
    t = title.strip().lower()
    if t.startswith("appendix"):
        return SectionKind.APPENDIX
    if t.startswith(("chapter", "part")):
        return SectionKind.CHAPTER if t.startswith("chapter") else SectionKind.PART
    return SectionKind.SECTION
