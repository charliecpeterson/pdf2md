from __future__ import annotations

import re

# Common OCR ligature breaks. Many PDF extractors emit a space where a ligature
# glyph existed, turning "efficient" into "e ffi cient" or "Cost-Effective" into
# "Cost-E ff ective". These regexes glue the pieces back together.
#
# Order matters: longer ligatures first so we don't half-match.
_LIGATURE_FIXES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?<=[A-Za-z])\s+ffi\s+(?=[a-z])"), "ffi"),
    (re.compile(r"(?<=[A-Za-z])\s+ffl\s+(?=[a-z])"), "ffl"),
    (re.compile(r"(?<=[A-Za-z])\s+ff\s+(?=[a-z])"), "ff"),
    (re.compile(r"(?<=[A-Za-z])\s+fi\s+(?=[a-z])"), "fi"),
    (re.compile(r"(?<=[A-Za-z])\s+fl\s+(?=[a-z])"), "fl"),
]

# Spaces inserted around subscripts/superscripts: "[Ln(NO 3 )] 2+" -> "[Ln(NO3)]2+"
# Tight a single-digit number that sits between a letter/closing-paren and either
# end-of-token, +/-, or closing paren.
_SUB_NUM_BEFORE_CLOSE = re.compile(r"(?<=[A-Za-z\)])\s+(\d{1,2})\s+(?=[\)\]\+\-])")
_SUP_AFTER_CLOSE = re.compile(r"(?<=[\)\]])\s+(\d{1,2})(?=[+\-])")

# Common Unicode normalizations
_UNICODE_FIXES = {
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "­": "",  # soft hyphen
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
}

# Inside-word hyphenation like "compu- tationally" → "computationally"
_HYPHEN_WRAP = re.compile(r"(?<=[a-z])-\s+(?=[a-z])")

# Excess inner whitespace (but preserve newlines)
_INNER_WS = re.compile(r"[ \t]+")

# Long runs of LaTeX thin-spaces (`\ ` repeated 5+ times) — OCR artifact found at the
# end of equation blocks when the engine padded out an equation number tail.
_LATEX_THINSPACE_RUN = re.compile(r"(?:\\\s){5,}")
# Long runs of any single non-letter character (e.g., `. . . . . .`, `- - - - - -`).
_PUNCT_RUN = re.compile(r"((?:[^\w\s]\s){10,})")
# Cap per-block text length — anything beyond this is almost certainly OCR garbage.
_BLOCK_TEXT_CAP = 4000


def normalize_text(text: str) -> str:
    """Clean up OCR artifacts: broken ligatures, sub/super spacing, soft hyphens, ligature unicode.

    Conservative: only touches predictable artifacts; leaves equations and code unchanged where
    we can tell. Does NOT modify newlines (preserves paragraph structure).
    Also caps OCR-garbage runs (long `\\ ` thin-space sequences, punctuation runs) and per-block length.
    """
    if not text:
        return text
    for k, v in _UNICODE_FIXES.items():
        if k in text:
            text = text.replace(k, v)
    for pat, repl in _LIGATURE_FIXES:
        text = pat.sub(repl, text)
    text = _SUB_NUM_BEFORE_CLOSE.sub(r"\1", text)
    text = _SUP_AFTER_CLOSE.sub(r"\1", text)
    text = _HYPHEN_WRAP.sub("", text)
    # Kill LaTeX thin-space runaway sequences (one of the OCR loop failure modes)
    text = _LATEX_THINSPACE_RUN.sub(" ", text)
    text = _PUNCT_RUN.sub(" ", text)
    # Collapse multi-space runs on each line without disturbing newlines
    lines = [_INNER_WS.sub(" ", line).rstrip() for line in text.split("\n")]
    out = "\n".join(lines).strip()
    # Hard length cap for a single block — anything past this is OCR noise
    if len(out) > _BLOCK_TEXT_CAP:
        out = out[:_BLOCK_TEXT_CAP] + "  /* truncated */"
    return out


def normalize_query(query: str) -> str:
    """Apply the same normalization to user queries so 'efficient' matches indexed text."""
    return normalize_text(query)


_OCR_PAIRWISE_SWAPS = [
    ("I", "1"), ("l", "1"), ("1", "I"),
    ("O", "0"), ("0", "O"),
    ("S", "5"), ("5", "S"),
]

_OCR_POS_SWAPS: dict[str, list[str]] = {
    "1": ["I", "l"], "I": ["1"], "l": ["1"],
    "0": ["O"], "O": ["0"],
    "5": ["S"], "S": ["5"],
}


def ocr_confusable_variants(num: str) -> list[str]:
    """Return OCR-confusable variants of a number string: 1↔I↔l, 0↔O, 5↔S.

    Includes per-position swaps (one substitution at a time), not just global
    replacements — so '1.1' → both '1.I' and 'I.1', covering the common case
    where OCR misreads only one of the digits.
    """
    variants: set[str] = {num}
    for a, b in _OCR_PAIRWISE_SWAPS:
        if a in num:
            variants.add(num.replace(a, b))
    for i, c in enumerate(num):
        for repl in _OCR_POS_SWAPS.get(c, []):
            variants.add(num[:i] + repl + num[i + 1:])
    return sorted(variants)
