"""Score how much of a block's text is real characters vs symbol-font garbage.

A PDF whose embedded font lacks a usable ToUnicode CMap extracts as dingbats and
glyph-name tokens (`❆ ♣/a114❛❝/a116✐❝❛❧` for "A practical guide") instead of
letters. Docling's default backend trusts such a text layer; pypdfium2 decodes the
same file correctly, so `enrich` can refill the block from the glyph layer — but
only once it knows the block is garbage. This module is that detector.

Deliberately limited to the symbol-substitution signal: the flagged ranges (misc
symbols, dingbats, enclosed alphanumerics, Private Use, geometric shapes) carry no
legitimate scientific text, so density there is unambiguous. Math operators,
arrows, and Greek are excluded on purpose — a vowel-ratio or dictionary check would
false-flag dense chemistry/math notation (`CSF`, `AQCC`, `1s2 2s2`) as illegible.
"""

from __future__ import annotations

import re

GARBAGE_BELOW = 0.6  # legibility under this means the text layer is symbol-font garbage

# Glyph-name tokens a broken font leaks in place of letters (`/a114`, `/a80`).
_GLYPH_NAME = re.compile(r"/a\d+")


def _is_substitute(c: str) -> bool:
    o = ord(c)
    return (
        0x2460 <= o <= 0x27BF  # enclosed alphanumerics, geometric shapes, misc symbols, dingbats
        or 0xE000 <= o <= 0xF8FF  # Private Use Area
        or c == "�"
    )


def score_legibility(text: str) -> float:
    """Fraction of letter-position characters that are real letters, in [0, 1].
    Returns 1.0 when there is no alphabetic content to judge (pure numerals or
    punctuation), so page numbers and equations don't read as garbage."""
    glyph_names = len(_GLYPH_NAME.findall(text))
    stripped = _GLYPH_NAME.sub("", text)
    suspect = glyph_names + sum(1 for c in stripped if _is_substitute(c))
    letters = sum(1 for c in stripped if c.isalpha())  # no substitute-range char is alpha
    base = letters + suspect
    if base == 0:
        return 1.0
    return letters / base


def is_garbage(text: str) -> bool:
    return score_legibility(text) < GARBAGE_BELOW
