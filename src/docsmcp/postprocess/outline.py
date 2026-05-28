from __future__ import annotations

import re
from typing import Iterable


# Pattern table: (kind, default_depth, regex). First-match-wins, so order matters.
_PATTERNS: list[tuple[str, int, re.Pattern[str]]] = [
    # Top-level grouping (highest in hierarchy when present)
    ("part", 1, re.compile(r"^\s*(?:PART|Part)\s+(?:\d+|[IVXLCDM]+)\b")),
    # Chapter (depth-2 only when a part is around; absolute depth defaults to 1)
    ("chapter", 1, re.compile(r"^\s*(?:Chapter|CHAPTER|Ch\.)\s+(?:\d+|[IVXLCDM]+)\b")),
    # Textbook exercise / problem labels
    ("exercise", 3, re.compile(r"^\s*E\s*\d+\.\d+\s*\([a-z]\)\b")),
    ("exercise", 3, re.compile(r"^\s*(?:E|Exercise)\s*\d+\.\d+\b")),
    ("problem", 3, re.compile(r"^\s*(?:P|Problem)\s*\d+\.\d+\b")),
    # Numbered hierarchy — order matters (deepest first)
    ("subsubsection", 3, re.compile(r"^\s*\d+\.\d+\.\d+\.?\s+\S")),
    ("subsection", 2, re.compile(r"^\s*\d+\.\d+\.?\s+\S")),
    ("section", 1, re.compile(r"^\s*\d+\.\s+\S")),
    # Roman-numeral section ("II. COMPUTATIONAL METHODS")
    ("section", 1, re.compile(r"^\s*[IVXLCDM]+\.\s+\S")),
    # Letter subsection ("A. Hydrides of silicon and carbon")
    ("subsection", 2, re.compile(r"^\s*[A-Z]\.\s+\S")),
    # Appendix
    ("appendix", 1, re.compile(r"^\s*(?:Appendix|APPENDIX)\s+(?:[A-Z]|\d+)\b")),
    # Figure / Table captions occasionally leak into headings
    ("figure_caption", 4, re.compile(r"^\s*(?:Figure|Fig\.?)\s+(?:\d+|[IVXLCDM]+)\b")),
    ("table_caption", 4, re.compile(r"^\s*(?:Table|TABLE)\s+(?:\d+|[IVXLCDM]+)\b")),
    # Front matter
    (
        "front_matter",
        1,
        re.compile(
            r"^\s*(?:Contents|Preface|Foreword|Acknowledgments?|Acknowledgements?|"
            r"References|Bibliography|Index|Abstract|Introduction|Conclusion[s]?|"
            r"Summary|Appendix)\s*$",
            re.I,
        ),
    ),
]

# Strip a leading bullet/marker before classification. ACS-style papers use ■
# as a top-level section marker; many style guides use ◆ ▶ ● ▪ similarly.
_LEADING_BULLET = re.compile(r"^\s*[■◆▶●▪✱*]+\s*")


_PARAGRAPH_OPENER = re.compile(r"^\([a-z]\)\s+\S")


def _is_noise(text: str) -> bool:
    """True if this 'heading' is really a sentence fragment that got misclassified.

    Heuristics: very long, many words, ends in period/colon and reads like prose,
    or is a clear paragraph opener like '(a) Hence ...'.
    """
    text = text.strip()
    if not text:
        return True
    if len(text) > 200:
        return True
    words = text.split()
    n_words = len(words)
    if n_words > 20:
        return True
    if _PARAGRAPH_OPENER.match(text) and n_words > 4:
        return True
    if text.endswith(".") and n_words > 10 and " " in text.rstrip("."):
        return True
    if text.endswith(":") and n_words > 10:
        return True
    # Looks like running text (lots of lowercase tokens, ends with punctuation)
    if n_words > 12:
        lowercase_word_count = sum(1 for w in words if w and w[0].islower())
        if lowercase_word_count / n_words > 0.7:
            return True
    return False


def classify_heading(text: str) -> tuple[str | None, int | None]:
    """Return (kind, depth) for a heading block, or (None, None) if it's noise.

    Depth is inferred from numbering when available (1.2.3 → depth 3); otherwise
    uses the pattern's default depth.
    """
    if not text:
        return None, None
    if _is_noise(text):
        return None, None

    stripped = text.strip()
    # Strip leading bullet/marker so the underlying heading text can be classified
    debulleted = _LEADING_BULLET.sub("", stripped).strip()
    candidate = debulleted if debulleted else stripped

    # Try pattern table
    for kind, default_depth, pat in _PATTERNS:
        if pat.match(candidate):
            depth = default_depth
            # Refine via numeric prefix when present
            nm = re.match(r"^\s*(\d+(?:\.\d+)*)\.?\s", candidate)
            if nm and kind in ("section", "subsection", "subsubsection"):
                depth = len(nm.group(1).split("."))
            return kind, depth

    # No pattern matched. Treat as plain heading at depth 1 if it looks heading-like.
    n_words = len(candidate.split())
    if n_words <= 12 and not candidate.endswith("."):
        return "heading", 1
    return None, None


_KIND_PARENT_DEPTH = {
    "part": 0,
    "chapter": 0,
    "section": 0,
    "subsection": 1,
    "subsubsection": 2,
    "exercise": 2,
    "problem": 2,
    "appendix": 0,
    "front_matter": 0,
    "figure_caption": 3,
    "table_caption": 3,
    "heading": 0,
}


def filter_outline(rows: Iterable[dict]) -> list[dict]:
    """Apply classify_heading to a sequence of heading rows, dropping noise.

    Adds simple contextual depth: when a `part` precedes chapters, the chapters
    nest under it (chapter depth becomes part_depth+1, etc.). Sections within
    a chapter likewise nest one level deeper.
    """
    out: list[dict] = []
    open_part: bool = False
    open_chapter: bool = False
    last_section_depth: int | None = None

    for r in rows:
        text = (r.get("text") or "").strip()
        kind, depth = classify_heading(text)
        if kind is None:
            continue

        if kind == "part":
            open_part = True
            open_chapter = False
            last_section_depth = None
        elif kind == "chapter":
            if open_part:
                depth = (depth or 1) + 1
            open_chapter = True
            last_section_depth = None
        elif kind == "section":
            base = depth or 1
            if open_chapter:
                base += 1
            elif open_part:
                base += 1
            depth = base
            last_section_depth = base
        elif kind in ("subsection", "subsubsection"):
            base = depth or 1
            if last_section_depth is not None:
                base = max(base, last_section_depth + 1)
            elif open_chapter:
                base += 1
            elif open_part:
                base += 1
            depth = base
        elif kind in ("exercise", "problem"):
            base = depth or 3
            if last_section_depth is not None:
                base = max(base, last_section_depth + 1)
            depth = base

        out.append(
            {
                "block_id": r["block_id"],
                "text": text,
                "page": r["page"],
                "depth": depth,
                "kind": kind,
            }
        )
    return out
