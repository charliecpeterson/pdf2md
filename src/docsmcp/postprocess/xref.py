from __future__ import annotations

import re


_EQ_REFS = re.compile(
    r"(?i)\b(?:eq(?:uation)?s?\.?)\s*\(?\s*([0-9]+(?:\s*[-–]\s*[0-9]+)?(?:\.[0-9]+)*[a-z]?)\s*\)?"
)
_TABLE_REFS = re.compile(
    r"(?i)\b(?:tables?|tab\.?)\s*([0-9]+(?:\.[0-9]+)*[a-z]?)"
)
_FIG_REFS = re.compile(
    r"(?i)\b(?:figures?|figs?\.?)\s*([0-9]+(?:\.[0-9]+)*[a-z]?)"
)


def _expand_range(token: str) -> list[str]:
    """Expand '15-18' to ['15','16','17','18']. Returns [token] for single values."""
    m = re.match(r"^\s*([0-9]+)\s*[-–]\s*([0-9]+)\s*$", token)
    if not m:
        return [token.strip()]
    a, b = int(m.group(1)), int(m.group(2))
    if a > b or b - a > 30:
        return [token.strip()]
    return [str(n) for n in range(a, b + 1)]


def extract_refs(text: str) -> dict[str, list[str]]:
    """Find inline references to equations, tables, and figures in text.

    Returns dict with keys "equations", "tables", "figures" → deduped lists of
    referenced numbers. Handles ranges like 'eq 15-18' by expanding to 15,16,17,18.
    """
    eqs: list[str] = []
    tabs: list[str] = []
    figs: list[str] = []
    for m in _EQ_REFS.finditer(text or ""):
        eqs.extend(_expand_range(m.group(1)))
    for m in _TABLE_REFS.finditer(text or ""):
        tabs.append(m.group(1).strip())
    for m in _FIG_REFS.finditer(text or ""):
        figs.append(m.group(1).strip())

    def _dedupe(xs: list[str]) -> list[str]:
        seen = set()
        out = []
        for x in xs:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    return {
        "equations": _dedupe(eqs),
        "tables": _dedupe(tabs),
        "figures": _dedupe(figs),
    }
