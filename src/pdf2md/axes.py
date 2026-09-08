"""Turning a plot's tick marks into a coordinate system.

Everything else in `digitize` reads drawn geometry in page points; this is what
makes those points mean a value. Two decisions here have cost real accuracy and
are worth reading before touching a threshold.

A log axis fits a line badly, so preferring a log fit that beats linear by 1e-6
is how 54/56/58 came back as "log" — log10 is locally linear over a narrow range
and printed ticks are a few percent uneven. `_LOG_MARGIN` exists for that.

And a figure with two y scales is drawn so a reader can tell which curve is
which, using colour. Reading only the left ticks shipped an Atkins ethanol curve
as 13.6-19.7 where it is 52.3-58.1, at confidence 1.0. `_right_axis_ticks` reads
text *objects*, because only an object carries a colour to match a series by.
"""

from __future__ import annotations

import ctypes
import math
import re
from typing import NamedTuple

import numpy as np
import pypdfium2.raw as C

from pdf2md.figure_geometry import (
    _fbox,
    _walk,
    fill_colour,
)

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

# How much better a log fit must be than a linear one to be believed.
_LOG_MARGIN = 0.01

def _textobj_str(obj, tp) -> str:
    n = C.FPDFTextObj_GetText(obj, tp, None, 0)
    if n <= 0:
        return ""
    buf = (ctypes.c_ushort * n)()
    C.FPDFTextObj_GetText(obj, tp, buf, n)
    return bytes(buf).decode("utf-16-le", errors="ignore").rstrip("\x00")

def _monotonic(vals) -> bool:
    return (all(a <= b for a, b in zip(vals, vals[1:]))
            or all(a >= b for a, b in zip(vals, vals[1:])))

def _linfit(vals, pos):
    ss_tot = float(((vals - vals.mean()) ** 2).sum())
    if ss_tot < 1e-9:  # ticks have no spread (all read the same value): nothing to calibrate against
        return (lambda p: float(vals.mean())), 0.0
    a, b = np.polyfit(pos, vals, 1)
    pred = a * pos + b
    r2 = 1.0 - float(((vals - pred) ** 2).sum()) / ss_tot
    return (lambda p: float(a * p + b)), r2


def _token_value(chars) -> float | None:
    """Numeric value of a group of (char, x, y, height). A trailing run that's smaller and
    raised is a superscript exponent, so matplotlib's log tick '10' + raised 'n' becomes
    10**n rather than the literal '10n'."""
    hmax = max(h for _, _, _, h in chars)
    ybase = min(y for _, _, y, _ in chars)
    base, exp = [], []
    for ch, _, y, h in chars:
        (exp if h < 0.8 * hmax and y > ybase + 1 else base).append(ch)
    bs, es = "".join(base), "".join(exp)
    if es:
        if bs == "10" and re.fullmatch(r"-?\d+", es):
            return 10.0 ** int(es)  # 10^n log tick
        return None                 # other superscript notation: too ambiguous to trust
    return float(bs) if _NUMBER.fullmatch(bs) else None

def _text_groups(page, region):
    """Every text cluster in the region as (chars, x_center, y_center) -- shared by tick
    reading and axis-label reading.

    Chars are clustered by position, not by pdfium's stream order: pdfium emits glyphs in
    drawing order with stray newlines between text runs, which splits a '10' from its raised
    exponent. Spatial grouping (a char joins a label on the same line just to its right)
    reassembles each label, superscript and all."""
    x0, x1, y0, y1 = region
    tp = page.get_textpage()
    chars = []
    for i in range(tp.count_chars()):
        ch = tp.get_text_range(i, 1)
        left, b, r, t = tp.get_charbox(i)
        if ch.strip():
            chars.append(("-" if ch == "−" else ch, (left + r) / 2, (b + t) / 2, t - b))  # U+2212 -> '-'
    # Agglomerate order-independently: a char joins a label if it's close to ANY glyph
    # already in it (same line within 5pt, centers within 9pt), and a char bridging two
    # groups merges them. This survives glyphs that sit off the baseline -- a low decimal
    # point, a raised exponent -- which order-dependent grouping mis-splits.
    groups: list[list] = []
    for c in chars:
        hits = [g for g in groups
                if any(abs(c[1] - d[1]) < 9 and abs(c[2] - d[2]) < 5 for d in g)]
        if not hits:
            groups.append([c])
            continue
        hits[0].append(c)
        for extra in hits[1:]:
            hits[0].extend(extra)
            groups.remove(extra)
    out = []
    for g in groups:
        mx = sum(c[1] for c in g) / len(g)
        my = sum(c[2] for c in g) / len(g)
        if x0 <= mx <= x1 and y0 <= my <= y1:
            out.append((g, mx, my))
    return out

def _object_text_groups(page, region):
    """_text_groups built from the text OBJECTS (container transform composed) instead of
    the page textpage: pdfium reports form-local charboxes for text nested inside a form
    XObject (a journal's embedded figure), so those tick labels are invisible to the
    charbox region filter. Object bounds ride the same _walk transform as the drawn
    paths. Grouping and the superscript test work at object granularity — a log tick's
    exponent is its own smaller, raised text object."""
    x0, x1, y0, y1 = region
    tp = page.get_textpage()
    items = []
    for o, container in _walk(page):
        if o.type != C.FPDF_PAGEOBJ_TEXT:
            continue
        s = _textobj_str(o, tp)
        if not s.strip():
            continue
        left, b, r, t = o.get_pos()
        ma, mb, mc, md, me, mf = container
        corners = [(ma * x + mc * y + me, mb * x + md * y + mf)
                   for x, y in ((left, b), (r, b), (left, t), (r, t))]
        cx = sum(x for x, _ in corners) / 4
        cy = sum(y for _, y in corners) / 4
        h = max(y for _, y in corners) - min(y for _, y in corners)
        items.append((s.replace("−", "-"), cx, cy, h))
    groups: list[list] = []
    for c in items:
        hits = [g for g in groups
                if any(abs(c[1] - d[1]) < 9 and abs(c[2] - d[2]) < 5 for d in g)]
        if not hits:
            groups.append([c])
            continue
        hits[0].append(c)
        for extra in hits[1:]:
            hits[0].extend(extra)
            groups.remove(extra)
    out = []
    for g in groups:
        mx = sum(c[1] for c in g) / len(g)
        my = sum(c[2] for c in g) / len(g)
        if x0 <= mx <= x1 and y0 <= my <= y1:
            out.append((g, mx, my))
    return out

def restore_signs(ticks):
    """The PDF text layer drops matplotlib's negative sign (an unmapped glyph), so -4
    reads as 4. A linear axis is monotonic in position; if the parsed values aren't but
    their magnitudes could be, negate one side of the zero to restore monotonicity.
    Assumes a standard (non-inverted) axis, so value rises with position; if both sides
    can be flipped it takes that increasing solution. A wrong guess on a genuinely
    inverted axis is the one case this can't self-flag, since the fit stays clean."""
    vals = [v for v, _ in ticks]
    if _monotonic(vals):
        return ticks, False
    zi = min(range(len(vals)), key=lambda i: abs(vals[i]))
    for flip in (set(range(zi)), set(range(zi + 1, len(vals)))):
        cand = [(-v if i in flip else v, p) for i, (v, p) in enumerate(ticks)]
        if _monotonic([v for v, _ in cand]):
            return cand, True  # inferred signs: an orientation guess -> caller should haircut trust
    return ticks, False

def restore_log_signs(ticks):
    """`restore_signs` for log-tick EXPONENTS: pdfium can drop the superscript minus too,
    reading 10^-3 as 10^3 — and since [1, 10, 100, 1000] descending down the axis is
    monotonic, the linear-sign repair can't see it. Ticks that are all integral powers of
    ten and DESCEND with position (a standard axis rises) flip their exponents' signs to
    the rising solution, flagged so the caller haircuts the trust. A genuinely inverted
    log axis is the one case this misreads, same trade as restore_signs."""
    vals = [v for v, _ in ticks]
    if len(vals) < 2 or any(v <= 0 for v in vals):
        return ticks, False
    exps = [math.log10(v) for v in vals]
    if any(abs(e - round(e)) > 1e-9 for e in exps):
        return ticks, False  # decimal log ticks (0.5, 0.05): not the superscript form
    if not all(a >= b for a, b in zip(vals, vals[1:])) or vals[0] == vals[-1]:
        return ticks, False
    return [(10.0 ** (-round(math.log10(v))), p) for v, p in ticks], True

def fit_axis(ticks):
    """Map an axis's ticks to a (position -> value) function plus an r2 and a kind. r2
    near 1 means the labels sit on a line we can trust; a low r2 flags a misread label.
    Tries a log fit too -- on a log axis the values are geometric but their logs are
    linear in position -- and takes whichever fits better, so a scientific log plot is
    read correctly instead of flagged."""
    vals = np.array([v for v, _ in ticks], dtype=float)
    pos = np.array([p for _, p in ticks])
    lin_map, lin_r2 = _linfit(vals, pos)
    if (vals > 0).all():
        log_map, log_r2 = _linfit(np.log10(vals), pos)
        # A real log axis fits terribly as a line -- decades apart, its linear r2
        # collapses -- so the log fit wins by a mile or not at all. A margin of 1e-6
        # let it win by a rounding error instead: the right-hand axis of Atkins
        # Fig. 5.1 reads 54, 56, 58, which is arithmetic, and came back "log" because
        # its printed ticks are 5% unevenly spaced and log10 is locally linear.
        if log_r2 > lin_r2 + _LOG_MARGIN:
            return (lambda p: 10.0 ** log_map(p)), log_r2, "log"
    return lin_map, lin_r2, "linear"

class _Calibration(NamedTuple):
    fx: object          # page-x -> data-x
    fy: object          # page-y -> data-y
    r2: float
    nticks: int
    x_kind: str         # "linear" | "log"
    y_kind: str
    flipped: bool       # a tick sign was inferred from monotonicity (hedge the trust)
    # The span of the tick VALUES the fit was made from. Data may run a little past
    # the outermost tick, but not by orders of magnitude: a mapping built from two
    # stray numbers can send a categorical axis to 1e8, and the tick span is the
    # only thing that says so.
    x_values: tuple[float, float] = (0.0, 0.0)
    y_values: tuple[float, float] = (0.0, 0.0)

def _drop_outlier(ticks):
    """Leave-one-out robustification: one stray in the tick band — an OCR fragment of a
    rotated axis title reading as '1', a misread label — wrecks an otherwise clean fit.
    With 4+ ticks, if dropping a single tick lifts a poor fit to a clean one (r2 >= .98),
    drop it. One outlier at most, so a genuinely bad axis stays bad and flagged."""
    _, r2, _ = fit_axis(ticks)
    if r2 >= 0.98 or len(ticks) < 4:
        return ticks
    best = (r2, ticks)
    for i in range(len(ticks)):
        cand = ticks[:i] + ticks[i + 1:]
        _, r2c, _ = fit_axis(cand)
        if r2c > best[0] + 1e-9:
            best = (r2c, cand)
    return best[1] if best[0] >= 0.98 else ticks

def _fit_ticks(frame, ticks):
    """(value, x, y) tick candidates in page points -> a _Calibration for `frame`, or
    None when there aren't two numeric ticks on each axis. Tick bands are bounded to the
    frame's own span so a neighboring subplot's labels can't leak in: x ticks sit below
    the frame's bottom edge within its width; y ticks sit left of its left edge within
    its height. Shared by tier 1 (text-layer ticks) and tier 1.5 (OCR'd ticks mapped
    back to page space)."""
    fx0, fx1, fy0, fy1 = _fbox(frame)
    fw, fh = fx1 - fx0, fy1 - fy0
    xticks = sorted([(v, mx) for v, mx, my in ticks
                     if fx0 - 2 <= mx <= fx1 + 0.05 * fw + 2
                     and fy0 - 0.35 * fh <= my < fy0], key=lambda t: t[1])
    yticks = sorted([(v, my) for v, mx, my in ticks
                     if fx0 - 0.4 * fw - 2 <= mx < fx0 - 2
                     and fy0 - 0.05 * fh <= my <= fy1 + 0.05 * fh], key=lambda t: t[1])
    if len(xticks) < 2 or len(yticks) < 2:
        return None
    xt, xflip = restore_signs(xticks)
    yt, yflip = restore_signs(yticks)
    xt = _drop_outlier(xt)
    yt = _drop_outlier(yt)
    xt, xflip2 = restore_log_signs(xt)
    yt, yflip2 = restore_log_signs(yt)
    fx, r2x, kx = fit_axis(xt)
    fy, r2y, ky = fit_axis(yt)
    xv = [v for v, _ in xt]
    yv = [v for v, _ in yt]
    return _Calibration(fx, fy, min(r2x, r2y), min(len(xt), len(yt)),
                        kx, ky, xflip or yflip or xflip2 or yflip2,
                        (min(xv), max(xv)), (min(yv), max(yv)))

def _right_axis_ticks(page, frame):
    """Numeric tick labels drawn to the RIGHT of a frame, with the colour they carry.

    `_fit_ticks` looks only left, and `_neighborhood` reaches barely past the frame's
    right edge -- both deliberate, to keep a neighbouring subplot's labels out of the
    fit. The cost was that a second y axis is invisible, so every series on a dual-axis
    figure got the left scale: Atkins Fig. 5.1 shipped its ethanol curve, truly 53.9 to
    58.2, as 13.6 to 19.7 at confidence 1.0.

    Bounded by the frame's own width, so this cannot reach a neighbouring panel either.
    Reads text OBJECTS rather than charboxes because only an object carries a colour,
    and colour is what says which curve belongs to this axis."""
    fx0, fx1, fy0, fy1 = _fbox(frame)
    fw, fh = fx1 - fx0, fy1 - fy0
    tp = page.get_textpage()
    out = []
    for o, container in _walk(page):
        if o.type != C.FPDF_PAGEOBJ_TEXT:
            continue
        text = _textobj_str(o, tp)
        if not text.strip():
            continue
        left, b, r, t = o.get_pos()
        ma, mb, mc, md, me, mf = container
        corners = [(ma * x + mc * y + me, mb * x + md * y + mf)
                   for x, y in ((left, b), (r, b), (left, t), (r, t))]
        mx = sum(x for x, _ in corners) / 4
        my = sum(y for _, y in corners) / 4
        if not (fx1 + 2 < mx <= fx1 + 0.4 * fw + 2):
            continue
        if not (fy0 - 0.05 * fh <= my <= fy1 + 0.05 * fh):
            continue
        # One text object is one label here, so the superscript reasoning in
        # `_token_value` (which needs per-char heights) has nothing to work with;
        # a plain numeral is all a right-hand tick ever is in the cases measured.
        stripped = text.strip().replace("\u2212", "-")
        if _NUMBER.fullmatch(stripped):
            out.append((float(stripped), my, fill_colour(o)))
    return out

def _fit_right_axis(ticks, frame, others=()):
    """`(position -> value, kind)` for a right-hand y axis, or None.

    `ticks` are `(value, page_x, page_y)`. None unless at least two of them sit in
    the band right of the frame and fit a line cleanly: a stray number beside a plot
    is not an axis, and inventing a second scale is worse than missing one. Shared by
    both tiers -- the vector one reads the ticks off text objects, the OCR one off the
    rendered crop -- because the geometry question is the same."""
    fx0, fx1, fy0, fy1 = _fbox(frame)
    fw, fh = fx1 - fx0, fy1 - fy0
    lefts = [_fbox(o)[0] for o in others if _fbox(o)[0] > fx1]
    mine = [(v, py) for v, px, py in ticks
            if fx1 + 2 < px <= fx1 + 0.4 * fw + 2
            and fy0 - 0.05 * fh <= py <= fy1 + 0.05 * fh
            # A tick nearer some other frame's left edge than this frame's right one
            # is that frame's y axis, not a second scale on this one. Side-by-side
            # panels put the next panel's axis squarely in this band: wires-2020
            # #/pictures/26 is two parity plots and was withheld for it, though it
            # scores 3 of 4 labelled anchors.
            and not any(abs(px - left) < px - fx1 for left in lefts)]
    if len(mine) < 2:
        return None
    pairs, _flipped = restore_signs(sorted(mine, key=lambda t: t[1]))
    fy, r2, kind = fit_axis(pairs)
    return (fy, kind) if r2 >= 0.98 else None
