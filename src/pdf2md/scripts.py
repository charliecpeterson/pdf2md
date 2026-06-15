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

import pypdfium2 as pdfium

# A char as (text, left, bottom, right, top) in PDF points (bottom-left origin).
Char = tuple[str, float, float, float, float]

_SMALL = 0.78   # a script glyph is shorter than this fraction of the cap height
_RAISE = 0.28   # superscript bottom sits this fraction of cap height above the baseline
_DROP = 0.15    # subscript bottom drops this fraction of cap height below the baseline


def _lines(chars: list[Char]) -> list[list[Char]]:
    cur: list[Char] = []
    out: list[list[Char]] = []
    last_b: float | None = None
    for ch in chars:
        if last_b is not None and abs(ch[2] - last_b) > 4:
            if cur:
                out.append(cur)
                cur = []
        cur.append(ch)
        last_b = ch[2]
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
    scored: list[tuple[str, str | None]] = []
    for text, _l, b, _r, t in line:
        flag = None
        # A script glyph is small AND its baseline is shifted off the line: raised
        # (superscript) or dropped below (subscript). Keying on the bottom edge,
        # not the top, is what separates a subscript from an ordinary x-height
        # letter (whose bottom still sits on the baseline).
        if text.isalnum() and cap > 0 and (t - b) < _SMALL * cap:
            if b > base + _RAISE * cap:
                flag = "sup"
            elif b < base - _DROP * cap:
                flag = "sub"
        scored.append((text, flag))
    return scored


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


def apply_scripts(text: str, scored: list[tuple[str, str | None]], *, escape: bool = False) -> str:
    if not text or not any(f for _, f in scored):
        return _join(text, [None] * len(text), escape)
    return _join(text, _align(text, scored), escape)


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
        self._chars: list[Char] = []
        for i in range(tp.count_chars()):
            box = tp.get_charbox(i)
            self._chars.append((tp.get_text_range(i, 1), *box))

    @property
    def empty(self) -> bool:
        return not self._chars

    def scored_region(self, bbox) -> list[tuple[str, str | None]]:
        """Detected (char, flag) for the glyphs whose centers fall inside `bbox`."""
        left, right = min(bbox.x0, bbox.x1), max(bbox.x0, bbox.x1)
        top, bottom = max(bbox.y0, bbox.y1), min(bbox.y0, bbox.y1)
        inside = [
            c for c in self._chars
            if left - 1 <= (c[1] + c[3]) / 2 <= right + 1
            and bottom - 1 <= (c[2] + c[4]) / 2 <= top + 1
        ]
        scored: list[tuple[str, str | None]] = []
        for line in _lines(inside):
            scored.extend(_score_line(line))
        return scored
