"""Recover inline sub/superscripts that Docling flattens.

Docling's `script` formatting is per-text-item, not per-inline-run, and pypdfium2's
`FPDFText_GetFontSize` returns garbage for the symbol/subset fonts common in
scientific PDFs. So we detect from glyph *geometry* instead: within a text line, a
character that is both smaller than the line's cap height and offset off the
baseline is a superscript (raised) or subscript (lowered). This reads what the PDF
physically shows rather than guessing semantics, and works only on born-digital
pages (scanned pages have no glyph boxes).

`apply_scripts` overlays the detected markup onto Docling's already-cleaned text by
aligning non-space characters; it only ever *inserts* `<sub>`/`<sup>` tags, never
changing the underlying characters, so a mis-detection degrades rendering, never
data.
"""

from __future__ import annotations

import statistics as st
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pypdfium2 as pdfium

# A char as (text, left, bottom, right, top) in PDF points (bottom-left origin).
Char = tuple[str, float, float, float, float]

_SMALL = 0.78   # a subscript glyph is shorter than this fraction of the cap height
_RAISE = 0.28   # superscript bottom sits this fraction of cap height above the baseline
_DROP = 0.15    # subscript bottom drops this fraction of cap height below the baseline
_DESCENDERS = frozenset("gjpqy")  # sit below the baseline naturally; never subscripts


def _lines(chars: list[Char]) -> list[list[Char]]:
    """Group chars into text lines by vertical overlap, so a raised superscript or
    dropped subscript stays attached to its line. A per-baseline split (the obvious
    approach) severs scripts onto their own line, where they no longer look small
    relative to a real baseline and go undetected."""
    out: list[list[Char]] = []
    cur: list[Char] = []
    lo = hi = None  # vertical band spanned by the current line's glyphs
    for ch in chars:
        _, _l, b, _r, t = ch
        if lo is not None and (t <= lo or b >= hi):
            out.append(cur)
            cur = []
            lo = hi = None
        cur.append(ch)
        if ch[0].strip():
            lo = b if lo is None else min(lo, b)
            hi = t if hi is None else max(hi, t)
    if cur:
        out.append(cur)
    return out


def _score_line(line: list[Char]) -> list[tuple[str, str | None]]:
    real = [c for c in line if c[0].strip()]
    if len(real) < 2:
        return [(c[0], None) for c in line]
    base = st.median([c[2] for c in real])
    tops = [c[4] for c in real if abs(c[2] - base) < 1.5]
    cap = (st.median(tops) - base) if tops else st.median([c[4] - c[2] for c in real])
    flags: list[str | None] = []
    for text, _l, b, _r, t in line:
        flag = None
        if cap > 0 and text.isalnum():
            # Raised off the baseline → superscript (nothing full-size sits above
            # the line, so size doesn't matter). Dropped below AND small AND not a
            # descender → subscript (the size + descender checks keep ordinary
            # x-height letters and glyph descenders out).
            if b > base + _RAISE * cap:
                flag = "sup"
            elif b < base - _DROP * cap and (t - b) < _SMALL * cap and text not in _DESCENDERS:
                flag = "sub"
        flags.append(flag)
    # Absorb a sign or symbol sitting in a neighbour's script band, so an exponent
    # like mol⁻¹ keeps its leading minus rather than dropping it.
    for i, (text, _l, b, _r, _t) in enumerate(line):
        if flags[i] or text.isalnum() or not text.strip() or cap <= 0:
            continue
        sides = (flags[i - 1] if i else None, flags[i + 1] if i + 1 < len(line) else None)
        if "sup" in sides and b > base + _RAISE * cap:
            flags[i] = "sup"
        elif "sub" in sides and b < base - _DROP * cap:
            flags[i] = "sub"
    return [(line[i][0], flags[i]) for i in range(len(line))]


def _esc(ch: str) -> str:
    return {"&": "&amp;", "<": "&lt;", ">": "&gt;"}.get(ch, ch)


def _align(text: str, scored: list[tuple[str, str | None]]) -> list[str | None]:
    """One flag per character of `text`, transferred from `scored` by matching
    non-space characters in order (tolerant of whitespace/ligature drift)."""
    flags: list[str | None] = [None] * len(text)
    si, n = 0, len(scored)
    for ti, ch in enumerate(text):
        if ch.isspace():
            continue
        while si < n and not scored[si][0].strip():
            si += 1
        if si < n and scored[si][0] == ch:
            flags[ti] = scored[si][1]
            si += 1
        else:  # resync: small look-ahead, else leave unmatched and don't consume
            for k in range(si + 1, min(si + 4, n)):
                if scored[k][0] == ch:
                    flags[ti] = scored[k][1]
                    si = k + 1
                    break
    return flags


def _unsplit_numbers(text: str, flags: list[str | None]) -> list[str | None]:
    """Clear a script flag that splits a numeric value. A digit raised or dropped
    inside a number (191.4 -> ¹91.4, 251.5 -> 25¹.5) is a misdetection: the cost of
    corrupting a value dwarfs the benefit. Within a run of digits/decimals, keep
    only a script group that is a clean trailing suffix (a real exponent or citation
    like 191.4⁶⁹); clear scripts that have baseline digits after them."""
    i, n = 0, len(text)
    while i < n:
        if not (text[i].isdigit() or text[i] in ".,"):
            i += 1
            continue
        j = i
        while j < n and (text[j].isdigit() or text[j] in ".,"):
            j += 1
        k = j  # back up over the trailing contiguous scripted group, which we keep
        while k > i and flags[k - 1] is not None:
            k -= 1
        for p in range(i, k):
            flags[p] = None
        i = j
    return flags


def apply_scripts(text: str, scored: list[tuple[str, str | None]], *, escape: bool = False) -> str:
    if not text or not any(f for _, f in scored):
        return _join(text, [None] * len(text), escape)
    return _join(text, _unsplit_numbers(text, _align(text, scored)), escape)


def _join(text: str, flags: list[str | None], escape: bool) -> str:
    out: list[str] = []
    open_f: str | None = None
    for ch, f in zip(text, flags):
        if f != open_f:
            if open_f:
                out.append(f"</{open_f}>")
            if f:
                out.append(f"<{f}>")
            open_f = f
        out.append(_esc(ch) if escape else ch)
    if open_f:
        out.append(f"</{open_f}>")
    return "".join(out)


class PageChars:
    """Per-page glyph geometry, extracted once and queried by bounding box."""

    def __init__(self, page: "pdfium.PdfPage") -> None:
        tp = page.get_textpage()
        n = tp.count_chars()
        # One text call for the whole page instead of one per char; fall back to
        # per-char only if the string and char count desync (rare encoded glyphs).
        full = tp.get_text_range()
        self.page_text: str = full  # pdfium's reading of the page (ligatures intact)
        if len(full) != n:
            full = None
        self._chars: list[Char] = [
            (full[i] if full is not None else tp.get_text_range(i, 1), *tp.get_charbox(i))
            for i in range(n)
        ]

    @property
    def empty(self) -> bool:
        return not self._chars

    def _region(self, bbox) -> list[Char]:
        left, right = min(bbox.x0, bbox.x1), max(bbox.x0, bbox.x1)
        top, bottom = max(bbox.y0, bbox.y1), min(bbox.y0, bbox.y1)
        return [
            c for c in self._chars
            if left - 1 <= (c[1] + c[3]) / 2 <= right + 1
            and bottom - 1 <= (c[2] + c[4]) / 2 <= top + 1
        ]

    def scored_region(self, bbox) -> list[tuple[str, str | None]]:
        """Detected (char, flag) for the glyphs whose centers fall inside `bbox`."""
        scored: list[tuple[str, str | None]] = []
        for line in _lines(self._region(bbox)):
            scored.extend(_score_line(line))
        return scored

    def text_region(self, bbox) -> str:
        """Raw text-layer string of the glyphs inside `bbox`, in reading order: the
        born-digital character truth used to cross-check an equation's LaTeX."""
        return "".join(c[0] for c in self._region(bbox))

    def reading_disorder(self, bbox) -> float:
        """How far the text layer's draw order departs from geometric reading order
        (0 = ordered). Some journals draw equation glyphs out of position, so the
        linear reading is scrambled token soup; this flags that the text is unfit to
        show as a transcription. Mean normalized displacement is robust to the local
        reordering that sub/superscripts cause; it catches the long-range scramble."""
        chars = [c for c in self._region(bbox) if c[0].strip()]
        n = len(chars)
        if n < 4:
            return 0.0
        cy = [(c[2] + c[4]) / 2 for c in chars]
        bands = sorted({round(y) for y in cy})
        band = lambda y: max(i for i, b in enumerate(bands) if y >= b - 3)
        order = sorted(range(n), key=lambda k: (-band(cy[k]), chars[k][1]))
        rank = {k: p for p, k in enumerate(order)}
        return sum(abs(k - rank[k]) for k in range(n)) / (n * n)
