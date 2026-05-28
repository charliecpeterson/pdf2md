from __future__ import annotations

import re

_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_ROMAN_RE = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)


def roman_to_int(s: str) -> int | None:
    """Convert a Roman numeral string to int. Returns None if invalid."""
    if not s or not _ROMAN_RE.match(s):
        return None
    s = s.upper()
    total = 0
    prev = 0
    for ch in reversed(s):
        v = _ROMAN_VALUES.get(ch, 0)
        if v == 0:
            return None
        if v < prev:
            total -= v
        else:
            total += v
        prev = v
    return total if total > 0 else None


def normalize_label_number(raw: str | None) -> str | None:
    """Normalize a captured number token to a canonical decimal string.

    Accepts Arabic (12, 2.46, 3.5a), pure Roman (I, III, IX, XII), and compound
    forms with Roman parts after dots (1.I, 1.III → 1.1, 1.3). Returns None on failure.
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    if "." in s:
        parts = []
        for piece in s.split("."):
            if not piece:
                continue
            if piece.isdigit():
                parts.append(piece)
                continue
            n = roman_to_int(piece)
            if n is not None:
                parts.append(str(n))
            else:
                parts.append(piece)
        return ".".join(parts)
    if s[0].isdigit():
        return s
    n = roman_to_int(s)
    if n is not None:
        return str(n)
    return s
