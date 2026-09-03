"""Extract plotted data from a born-digital vector chart by reading the drawn path
coordinates out of the PDF (near-lossless), not by tracing pixels. Only pypdfium2 --
the engine already in use -- touches the PDF, so there is no new dependency, and a
raster or scanned figure (no vector paths) simply yields nothing and stays a crop.

The recovered data is only as trustworthy as the calibration, so every result carries
a confidence: whether an axes frame and enough numeric ticks were found, and how
cleanly those ticks fit a line. That rides into the markdown/README (flag-don't-
fabricate) so a human or an LLM reading the output knows how far to trust the numbers.

This is the first and most accurate tier of the ladder. The VLM-assisted tier for
raster plots lives in `digitize_vlm.py` behind the same `Digitization` return type;
it shares none of this module's machinery, which is why it is not in this file.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import math
import re
from collections import Counter
from pathlib import Path
from typing import NamedTuple, Protocol, runtime_checkable

import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as C

from pdf2md.figure_geometry import (
    _assign,
    _axes_frames,
    _fbox,
    _has_raster_image,
    _is_rect,
    _page_forms,
    _polylines,
    _walk,
    coloured_polylines,
    fill_colour,
)
from pdf2md.schema import BBox, Digitization

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
# How much better a log fit must be than a linear one to be believed.
_LOG_MARGIN = 0.01
# Share of a path's segments that must run along or up before it reads as a grid.
_AXIS_ALIGNED_SHARE = 0.9


@runtime_checkable
class Digitizer(Protocol):
    def digitize(self, pdf_path: Path, page: int, bbox: BBox) -> Digitization | None: ...
























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
        l, b, r, t = tp.get_charbox(i)
        if ch.strip():
            chars.append(("-" if ch == "−" else ch, (l + r) / 2, (b + t) / 2, t - b))  # U+2212 -> '-'
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


def _textobj_str(obj, tp) -> str:
    n = C.FPDFTextObj_GetText(obj, tp, None, 0)
    if n <= 0:
        return ""
    buf = (ctypes.c_ushort * n)()
    C.FPDFTextObj_GetText(obj, tp, buf, n)
    return bytes(buf).decode("utf-16-le", errors="ignore").rstrip("\x00")


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
        l, b, r, t = o.get_pos()
        ma, mb, mc, md, me, mf = container
        corners = [(ma * x + mc * y + me, mb * x + md * y + mf)
                   for x, y in ((l, b), (r, b), (l, t), (r, t))]
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


def _merged_text_groups(page, region):
    """Char-accurate textpage groups first, object-level groups added only where the
    textpage saw nothing (nested-form text) — near-duplicates dropped by position."""
    groups = _text_groups(page, region)
    extra = [t for t in _object_text_groups(page, region)
             if not any(abs(t[1] - g[1]) < 3 and abs(t[2] - g[2]) < 3 for g in groups)]
    return groups + extra


def _groups_in_region(groups, region):
    x0, x1, y0, y1 = region
    return [group for group in groups if x0 <= group[1] <= x1 and y0 <= group[2] <= y1]


def _monotonic(vals) -> bool:
    return (all(a <= b for a, b in zip(vals, vals[1:]))
            or all(a >= b for a, b in zip(vals, vals[1:])))


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


def _linfit(vals, pos):
    ss_tot = float(((vals - vals.mean()) ** 2).sum())
    if ss_tot < 1e-9:  # ticks have no spread (all read the same value): nothing to calibrate against
        return (lambda p: float(vals.mean())), 0.0
    a, b = np.polyfit(pos, vals, 1)
    pred = a * pos + b
    r2 = 1.0 - float(((vals - pred) ** 2).sum()) / ss_tot
    return (lambda p: float(a * p + b)), r2


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


def _calibrate(page, frame, region):
    """Tier 1 calibration: tick values from the page's text (chars, plus object-level
    text for form-nested labels), fitted by _fit_ticks."""
    return _calibrate_groups(frame, _merged_text_groups(page, region))


def _calibrate_groups(frame, groups):
    ticks = [(v, mx, my) for g, mx, my in groups if (v := _token_value(g)) is not None]
    return _fit_ticks(frame, ticks)


# How far off the frame's own edge a vertex may sit and still count as on it.
# The border is drawn on the edge; a data point that merely touches it arrives
# with company, which is what `_traces_the_frame` requires.
_ON_FRAME_PT = 0.75


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
        l, b, r, t = o.get_pos()
        ma, mb, mc, md, me, mf = container
        corners = [(ma * x + mc * y + me, mb * x + md * y + mf)
                   for x, y in ((l, b), (r, b), (l, t), (r, t))]
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


def _second_y_axis(page, frame):
    """`(position -> value, kind, tick colour)` for a right-hand y axis, or None.

    None unless at least two right-side ticks fit a line cleanly: a stray number
    beside a plot is not an axis, and inventing a second scale is worse than missing
    one."""
    ticks = _right_axis_ticks(page, frame)
    if len(ticks) < 2:
        return None
    pairs, _flipped = restore_signs(sorted(((v, p) for v, p, _c in ticks), key=lambda t: t[1]))
    fy, r2, kind = fit_axis(pairs)
    if r2 < 0.98:
        return None
    colours = Counter(c for _v, _p, c in ticks if c is not None)
    return fy, kind, (colours.most_common(1)[0][0] if colours else None)


def _traces_the_frame(poly, frame) -> bool:
    """Whether every vertex of `poly` sits on the frame's border.

    `_is_rect` catches a border drawn as one rectangle. GRASP2018 #/pictures/3
    draws it as two triangles instead, and those sailed through every guard:
    they span the full plot width, they are not flat, they sit inside the tick
    range by construction, and their calibration is perfect. The figure shipped
    the axes box as its data at confidence 0.9, with the hundred printed scatter
    points absent. Nothing but the vertices themselves says so."""
    x0, x1, y0, y1 = _fbox(frame)
    return all(
        min(abs(x - x0), abs(x - x1)) <= _ON_FRAME_PT
        or min(abs(y - y0), abs(y - y1)) <= _ON_FRAME_PT
        for x, y in poly
    )


def _axis_aligned(poly) -> bool:
    """Whether the path only ever runs along or up -- a grid, not a curve.

    A drawn grid is one path of closed rectangles: Atkins Fig. 3.4's is
    `(421.2, 703.1) -> (458.4, 703.1) -> (458.4, 665.4) -> (421.2, 665.4) -> ...`.
    `_is_rect` misses it because it is many rectangles rather than one, and it spans
    the plot and is not flat, so nothing else stopped it either -- that figure shipped
    its gridlines as a 60-point series at confidence 0.999.

    A *share* rather than all of them, because concatenating disjoint subpaths into one
    point list leaves a jump between each rectangle and the next: 57 of that path's 59
    segments are axis-aligned and the 2 that are not are those jumps. Measured over
    every candidate path in the labelled figures, the distribution is bimodal -- 45 at
    or below 0.3 and 12 at 1.0, with nothing at all between 0.6 and 1.0 -- so the rule
    sits in an empty band rather than on a judgement call.

    Bars are axis-aligned too. Removing them here is what lets them reach
    `_bar_series`, which reads them from the rectangles properly."""
    segments = [(abs(b[0] - a[0]), abs(b[1] - a[1])) for a, b in zip(poly, poly[1:])]
    if len(segments) < 2:
        return False
    aligned = sum(1 for dx, dy in segments if dx < 1e-6 or dy < 1e-6)
    return aligned / len(segments) >= _AXIS_ALIGNED_SHARE


def _data_series(polys, frame, fx, fy) -> list[list[tuple[float, float]]]:
    """Curves that span most of the plot width: the actual data traces, not markers
    (small) or gridlines (flat). Each vertex is mapped to data coordinates."""
    fw = max(x for x, _ in frame) - min(x for x, _ in frame)
    out = []
    for p in polys:
        if _is_rect(p) or _traces_the_frame(p, frame) or _axis_aligned(p):
            continue
        xext = max(x for x, _ in p) - min(x for x, _ in p)
        yext = max(y for _, y in p) - min(y for _, y in p)
        if xext < 0.5 * fw or yext < 1e-6:  # too short to be data, or a flat gridline
            continue
        out.append([(round(float(fx(x)), 4), round(float(fy(y)), 4)) for x, y in p])
    return out










def _marker_series(forms, frame, fx, fy) -> list[list[tuple[float, float]]]:
    """A scatter plot draws no connecting line -- each point is a small marker, which
    matplotlib stamps as a form XObject. Take the bbox centre of every small form assigned
    to the plot frame as a data point, grouped into series by marker appearance (size +
    color); groups too small to be a series (< 3 points, e.g. a legend swatch) are dropped."""
    fx0, fx1, fy0, fy1 = _fbox(frame)
    fw, fh = fx1 - fx0, fy1 - fy0
    groups: dict[tuple, list] = {}
    for cx, cy, w, h, style in forms:
        if w < 0.1 * fw and h < 0.1 * fh and fx0 - 1 <= cx <= fx1 + 1 and fy0 - 1 <= cy <= fy1 + 1:
            groups.setdefault(style, []).append((cx, cy))
    series = [sorted((round(float(fx(cx)), 4), round(float(fy(cy)), 4)) for cx, cy in pts)
              for pts in groups.values() if len(pts) >= 3]
    return series


def _bar_series(polys, frame, fx, fy) -> list[list[tuple[float, float]]]:
    """A bar chart draws each bar as a filled rect standing on a common baseline inside
    the frame. Take rects whose bottoms align (within 2% of the frame height) on the most
    common baseline; each contributes (x center, top value). Horizontal bars, stacked
    bars, and grouped multi-series bars aren't separated (documented limits)."""
    fx0, fx1 = min(x for x, _ in frame), max(x for x, _ in frame)
    fy0, fy1 = min(y for _, y in frame), max(y for _, y in frame)
    fw, fh = fx1 - fx0, fy1 - fy0
    rects = []
    for p in polys:
        if not _is_rect(p):
            continue
        x0, x1 = min(x for x, _ in p), max(x for x, _ in p)
        y0, y1 = min(y for _, y in p), max(y for _, y in p)
        w, h = x1 - x0, y1 - y0
        # a bar: narrower than half the frame, inside it, not the frame/legend-box shape
        if w < 0.5 * fw and h > 0.01 * fh and fx0 - 1 <= x0 and x1 <= fx1 + 1 and fy0 - 1 <= y0:
            rects.append(((x0 + x1) / 2, y0, y1))
    if len(rects) < 2:
        return []
    baselines = sorted(r[1] for r in rects)
    base = baselines[len(baselines) // 2]  # the common footing (median bottom)
    bars = [(cx, top) for cx, bot, top in rects if abs(bot - base) <= 0.02 * fh]
    if len(bars) < 2:
        return []
    return [sorted((round(float(fx(cx)), 4), round(float(fy(top)), 4)) for cx, top in bars)]



def _neighborhood(frame, region):
    """The band of page around a frame where its own tick labels live — passed to
    _calibrate as the text region so adjacent subplots' labels stay out of the fit."""
    l, r, b, t = _fbox(frame)
    fw, fh = r - l, t - b
    return (max(region[0], l - 0.4 * fw), min(region[1], r + 0.08 * fw),
            max(region[2], b - 0.35 * fh), min(region[3], t + 0.08 * fh))


_PANEL_FLOOR = 0.5  # a panel calibrated worse than this poisons the whole figure: drop it


@dataclass
class _VectorGeometry:
    region: tuple[float, float, float, float]
    polylines: list[list[tuple[float, float]]]
    frames: list[list[tuple[float, float]]]
    forms: list | None = None


def _tick_range_fraction(series, cal) -> float:
    """Fraction of emitted points that sit within reach of the ticks they were mapped
    from, the vector path's version of the check `_in_range_fraction` already applies
    to a VLM read.

    Deliberately generous -- a whole tick span of slack on each side -- because it is
    not policing a curve that runs past the last tick. It is there for a mapping that
    is wrong by orders of magnitude: cjk-sample #/pictures/8 has a categorical y axis
    (Text, Speech, Prosody), and two stray numbers near it produced a fit that put the
    emitted values between -1.5e8 and -1.3e7. Against that, a correct read like Atkins
    #/pictures/948 (y ticks 0..40, data 0.35..43.07) sits at 1.08 of its span, so any
    rule between them separates the two; the loose end is chosen so the check can only
    ever catch the indefensible."""
    def within(values, span):
        lo, hi = sorted(span)
        width = hi - lo
        if width <= 0:
            return None
        return sum(1 for v in values if lo - width <= v <= hi + width)

    xs = [p[0] for s in series for p in s]
    ys = [p[1] for s in series for p in s]
    if not xs:
        return 1.0
    counts = [c for c in (within(xs, cal.x_values), within(ys, cal.y_values)) if c is not None]
    return min(counts) / len(xs) if counts else 1.0


def _panel_series(pg, panels, polys, forms, colours=None):
    """Extract each calibrated panel's series. Returns (series, per-series names,
    kinds, per-panel confidences, r2s, used calibrations, in-tick-range fractions,
    skipped); a panel whose own
    calibration confidence is below _PANEL_FLOOR is skipped (counted in `skipped`) so
    one misread inset can't drag every other panel's good data under the emit floor.
    Names are None-worthy only by the caller (single-panel figures don't need them).

    The used calibrations come back because the caller has to describe the data it is
    shipping. It used to take the note's r2, tick count and axis kinds from
    `panels[0]`, which is only the data's own calibration when the first frame
    happened to produce series -- cjk-sample #/pictures/8 shipped 15 points at
    confidence 0.667 under a note reading `fit R^2=0.000`, because the two came from
    different frames."""
    frames = [fr for fr, _ in panels]
    poly_of = _assign([(sum(x for x, _ in p) / len(p), sum(y for _, y in p) / len(p))
                       for p in polys], frames)
    form_of = _assign([(f[0], f[1]) for f in forms], frames)
    out_series, names, kinds, confs, r2s, cals, insides = [], [], [], [], [], [], []
    used = skipped = 0
    for i, (frame, cal) in enumerate(panels):
        confidence = round(max(0.0, min(1.0, cal.r2)) * min(1.0, cal.nticks / 3), 3)
        if cal.flipped:  # signs inferred from monotonicity: usually right, but an inverted axis
            confidence = round(confidence * 0.7, 3)  # can't self-check, so hedge (don't cripple)
        if len(panels) > 1 and confidence < _PANEL_FLOOR:
            skipped += 1
            continue
        mine = [p for p, w in zip(polys, poly_of) if w == i]
        mine_colours = ([c for c, w in zip(colours, poly_of) if w == i]
                        if colours is not None else [None] * len(mine))
        # A figure with two y scales is drawn so a reader can tell which curve
        # belongs to which, and colour is how: on Atkins Fig. 5.1 the right ticks,
        # the word "Ethanol" and its curve are all (113, 45, 125). Without this the
        # ethanol curve shipped on the water scale -- 13.6 to 19.7 for a quantity
        # that runs 53.9 to 58.2 -- at confidence 1.0.
        second = _second_y_axis(pg, frame) if colours is not None else None
        right_colour = second[2] if second else None
        theirs = ([p for p, c in zip(mine, mine_colours) if c == right_colour]
                  if right_colour else [])
        if theirs:
            mine = [p for p, c in zip(mine, mine_colours) if c != right_colour]
        series = _data_series(mine, frame, cal.fx, cal.fy)
        kind = "line"
        if not series:  # no connecting line -> try scatter markers
            series = _marker_series([f for f, w in zip(forms, form_of) if w == i],
                                    frame, cal.fx, cal.fy)
            kind = "scatter"
        if not series:  # no markers either -> try bars on a common baseline
            series = _bar_series(mine, frame, cal.fx, cal.fy)
            kind = "bar"
        if not series:
            continue
        # A mapping can be arithmetically perfect and still send the data nowhere
        # near the ticks it was built from. Two ticks always fit a line exactly, so
        # r2 cannot object to that; this can. Per panel, because each panel has its
        # own axes -- checking every panel's series against the first panel's ticks
        # convicts a perfectly good second panel with a different y range.
        # Measured before the right axis's series join, because they are mapped with
        # a different y scale and comparing them to this axis's ticks would convict
        # them for being on the axis they belong to.
        inside = _tick_range_fraction(series, cal) if series else 1.0
        if inside < 1.0:
            confidence = round(confidence * inside, 3)
        if theirs:
            series += _data_series(theirs, frame, cal.fx, second[0])
        used += 1
        confs.append(confidence)
        r2s.append(cal.r2)
        cals.append(cal)
        insides.append(inside)
        kinds.append(kind)
        # a panel whose axis scale differs from the figure's first panel says so in its
        # series names, since Digitization carries one x_kind/y_kind pair
        scale = ("" if (cal.x_kind, cal.y_kind) == (panels[0][1].x_kind, panels[0][1].y_kind)
                 else f" ({cal.x_kind} x, {cal.y_kind} y)")
        names += [f"panel {used} series {j}{scale}" for j in range(1, len(series) + 1)]
        out_series += series
    return out_series, names, kinds, confs, r2s, cals, insides, skipped


def _has_series_geometry(geometry: _VectorGeometry) -> bool:
    """Whether any candidate frame could yield line, scatter, or bar data.

    Calibration only maps page coordinates to values. These three extractors decide
    whether series exist from geometry alone, so an empty result here proves OCR-read
    ticks cannot make the figure recoverable.
    """
    frames = geometry.frames
    forms = geometry.forms or []
    poly_of = _assign(
        [
            (sum(x for x, _ in polyline) / len(polyline),
             sum(y for _, y in polyline) / len(polyline))
            for polyline in geometry.polylines
        ],
        frames,
    )
    form_of = _assign([(form[0], form[1]) for form in forms], frames)
    identity = lambda value: value
    for index, frame in enumerate(frames):
        polylines = [
            polyline
            for polyline, owner in zip(geometry.polylines, poly_of)
            if owner == index
        ]
        panel_forms = [form for form, owner in zip(forms, form_of) if owner == index]
        if (
            _data_series(polylines, frame, identity, identity)
            or _marker_series(panel_forms, frame, identity, identity)
            or _bar_series(polylines, frame, identity, identity)
        ):
            return True
    return False


def _dominant_kind(kinds: list[str]) -> str:
    """Choose one renderer for mixed panels, with stable line-first tie breaking."""
    return max(("line", "scatter", "bar"), key=kinds.count)


def _crop_to_page(page_size, region, crop_path: Path, padding_pts: float):
    """Affine crop-pixel -> page-point mapping, reconstructed from the crop's render
    geometry: CropRenderer rendered the clamped, padded region, so the image's own size
    against that box gives the scale, no dpi bookkeeping needed."""
    from PIL import Image

    w, h = page_size
    x0, x1, y0, y1 = region
    left = max(0.0, x0 - padding_pts)
    right = min(w, x1 + padding_pts)
    top = max(0.0, h - y1 - padding_pts)
    bottom = min(h, h - y0 + padding_pts)
    with Image.open(crop_path) as im:
        pw, ph = im.size
    sx = (right - left) / pw
    sy = (bottom - top) / ph
    return lambda px, py: (left + px * sx, h - (top + py * sy))


def vector_ocr_digitize(pdf_path: Path, page: int, bbox: BBox, crop_path: Path, reader,
                        padding_pts: float = 6.0) -> Digitization | None:
    """Tier 1.5: exact vector curve geometry + OCR-read axes, for figures whose tick text
    is OUTLINED to paths (journals often convert figure fonts to outlines, leaving no
    text for tier 1 to calibrate against). The drawn curves are still exact vector paths;
    the missing tick values are OCR'd off the rendered crop and mapped back to page
    space, where the same per-frame band/fit machinery as tier 1 calibrates each panel —
    multi-panel figures included. Geometry stays near-lossless; confidence takes the OCR
    haircut, and below the print floor the figure stays a crop (an opt-in VLM tier can
    still try)."""
    if reader is None:
        return None
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        return vector_ocr_digitize_page(
            pdf[page - 1], bbox, crop_path, reader, padding_pts=padding_pts
        )
    finally:
        pdf.close()


def vector_ocr_digitize_page(
    page,
    bbox: BBox,
    crop_path: Path,
    reader,
    padding_pts: float = 6.0,
    *,
    geometry: _VectorGeometry | None = None,
) -> Digitization | None:
    """Run outlined-axis recovery against an already-open PDF page."""
    from pdf2md.calibrate import tick_value

    if reader is None:
        return None
    if geometry is None:
        region = (min(bbox.x0, bbox.x1), max(bbox.x0, bbox.x1),
                  min(bbox.y0, bbox.y1), max(bbox.y0, bbox.y1))
        polys = _polylines(page, region)
        frames = _axes_frames(polys, region)
    else:
        region = geometry.region
        polys = geometry.polylines
        frames = geometry.frames
    if not frames:  # cheap vector check first: no frame, no OCR spent
        return None
    from PIL import Image

    to_page = _crop_to_page(page.get_size(), region, Path(crop_path), padding_pts)
    res = reader(np.asarray(Image.open(crop_path).convert("RGB")))
    ticks = []
    if res is not None and getattr(res, "txts", None):
        for t, s, box in zip(res.txts, res.scores, res.boxes):
            if s >= 0.5 and (v := tick_value(t)) is not None:
                cx = float(np.mean([p[0] for p in box]))
                cy = float(np.mean([p[1] for p in box]))
                ticks.append((v, *to_page(cx, cy)))
    panels = [(fr, cal) for fr in frames if (cal := _fit_ticks(fr, ticks)) is not None]
    if not panels:
        return None
    forms = (
        geometry.forms
        if geometry is not None and geometry.forms is not None
        else _page_forms(page, region)
    )
    series, names, kinds, confs, r2s, cals, insides, skipped = _panel_series(
        page, panels, polys, forms
    )
    if not series:
        return None
    confidence = round(min(confs) * 0.9, 3)  # OCR-read ticks: haircut vs the text layer
    npts = sum(len(s) for s in series)
    multi = len(confs) > 1
    note = ("vector curve paths with OCR-read axes (tick text is outlined to paths; "
            "axis numbers read off the rendered crop), "
            + (f"{len(confs)} panels, " if multi else "")
            + f"{len(series)} series / {npts} points (min fit R^2={min(r2s):.3f})"
            + (f"; {skipped} weakly-calibrated panel(s) skipped" if skipped else "")
            + " — verify the axis ranges against the image")
    # A candidate below the floor is returned rather than dropped, so the figure
    # records that one existed and was withheld. Returning None instead left it
    # reading as "the axis calibration failed", which is a different and untrue
    # statement -- the tick-range check moved six Atkins figures into that lie
    # before this, among them #/pictures/96, whose data ran to x -1243..1693
    # against a printed axis of 0..450.
    return Digitization(series, "vector-path/ocr-axes", confidence, note,
                        x_kind=cals[0].x_kind, y_kind=cals[0].y_kind,
                        kind=_dominant_kind(kinds),
                        series_names=names if multi else None)


class VectorPathDigitizer:
    """Recover data from a born-digital vector chart via its drawn path coordinates.
    Multi-panel figures (subplots, insets) digitize per panel: every frame that
    calibrates against its own neighboring ticks contributes its series, tagged with
    the panel in `series_names`."""

    def digitize(self, pdf_path: Path, page: int, bbox: BBox) -> Digitization | None:
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            return self.digitize_page(pdf[page - 1], bbox)
        finally:
            pdf.close()

    def digitize_page(self, page, bbox: BBox) -> Digitization | None:
        """Recover chart data from an already-open PDF page."""
        return self.digitize_page_with_geometry(page, bbox)[0]

    def digitize_page_with_geometry(
        self, page, bbox: BBox
    ) -> tuple[Digitization | None, _VectorGeometry | None]:
        """Return reusable vector geometry when outlined axes may need OCR."""
        region = (min(bbox.x0, bbox.x1), max(bbox.x0, bbox.x1),
                  min(bbox.y0, bbox.y1), max(bbox.y0, bbox.y1))
        polys = _polylines(page, region)
        frames = _axes_frames(polys, region)
        if not frames:
            return None, None
        geometry = _VectorGeometry(region, polys, frames)
        text_groups = _merged_text_groups(page, region)
        panels = []
        for fr in frames:
            cal = _calibrate_groups(
                fr, _groups_in_region(text_groups, _neighborhood(fr, region))
            )
            if cal is not None:  # a legend box has no ticks and drops out here
                panels.append((fr, cal))
        if not panels:
            return None, geometry
        forms = _page_forms(page, region)
        geometry.forms = forms
        polys_with_colour = coloured_polylines(page, region)
        series, names, kinds, confs, r2s, cals, insides, skipped = _panel_series(
            page, panels, polys, forms, colours=[c for _pts, c in polys_with_colour]
        )
        if not series:
            return None, geometry
        cal0 = cals[0]  # the calibration the shipped data came from, not panels[0]
        confidence = min(confs)
        inside = min(insides) if insides else 1.0
        kind = _dominant_kind(kinds)
        npts = sum(len(s) for s in series)
        if len(confs) == 1:  # single panel: the common case, same note as always
            what = (f"{len(series)} series" if kind == "line"
                    else f"{len(series)} series / {npts} {kind} points" if len(series) > 1
                    else f"{npts} {kind} points")
            axes = cal0.x_kind if cal0.x_kind == cal0.y_kind else f"{cal0.x_kind}/{cal0.y_kind}"
            note = (f"vector paths, {what}; {axes} axes calibrated on {cal0.nticks}+ ticks/axis "
                    f"(fit R^2={cal0.r2:.3f}{'; signs inferred' if cal0.flipped else ''}"
                    f"{'' if cal0.nticks >= 3 else '; two ticks fit a line exactly, so that R^2 is not evidence'})"
                    + ("" if inside >= 1.0 else
                       f"; {100 - round(inside * 100)}% of points fall outside the tick range"))
            return Digitization(
                series, "vector-path", confidence, note,
                x_kind=cal0.x_kind, y_kind=cal0.y_kind, kind=kind,
            ), None
        drop = f"; {skipped} weakly-calibrated panel(s) skipped — read those off the image" if skipped else ""
        note = (f"vector paths, {len(confs)} panels, {len(series)} series / {npts} points; "
                f"axes calibrated per panel (min fit R^2={min(r2s):.3f}){drop}")
        return Digitization(
            series, "vector-path", confidence, note,
            x_kind=cal0.x_kind, y_kind=cal0.y_kind, kind=kind,
            series_names=names,
        ), None

    def has_series_geometry(self, page, geometry: _VectorGeometry) -> bool:
        if geometry.forms is None:
            geometry.forms = _page_forms(page, geometry.region)
        return _has_series_geometry(geometry)

    def has_raster_image(self, page, bbox: BBox) -> bool:
        region = (
            min(bbox.x0, bbox.x1),
            max(bbox.x0, bbox.x1),
            min(bbox.y0, bbox.y1),
            max(bbox.y0, bbox.y1),
        )
        return _has_raster_image(page, region)
