"""Extract plotted data from a born-digital vector chart by reading the drawn path
coordinates out of the PDF (near-lossless), not by tracing pixels. Only pypdfium2 --
the engine already in use -- touches the PDF, so there is no new dependency, and a
raster or scanned figure (no vector paths) simply yields nothing and stays a crop.

The recovered data is only as trustworthy as the calibration, so every result carries
a confidence: whether an axes frame and enough numeric ticks were found, and how
cleanly those ticks fit a line. That rides into the markdown/README (flag-don't-
fabricate) so a human or an LLM reading the output knows how far to trust the numbers.

This is the first, most accurate tier of a planned ladder; a VLM-assisted tier (for
raster plots, lower confidence) can slot in behind the same `Digitizer` seam.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import NamedTuple, Protocol, runtime_checkable

import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as C

from pdf2md.schema import BBox, Digitization

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


@runtime_checkable
class Digitizer(Protocol):
    def digitize(self, pdf_path: Path, page: int, bbox: BBox) -> Digitization | None: ...


_IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _compose(outer, inner):
    """outer ∘ inner as 6-tuple affine matrices (inner applies first)."""
    a2, b2, c2, d2, e2, f2 = outer
    a1, b1, c1, d1, e1, f1 = inner
    return (a2 * a1 + c2 * b1, b2 * a1 + d2 * b1,
            a2 * c1 + c2 * d1, b2 * c1 + d2 * d1,
            a2 * e1 + c2 * f1 + e2, b2 * e1 + d2 * f1 + f2)


def _mat(o):
    m = o.get_matrix()
    return (m.a, m.b, m.c, m.d, m.e, m.f)


def _walk(page):
    """Yield (obj, container transform to page space). Content nested inside form
    XObjects — a matplotlib figure embedded by LaTeX is one — carries form-local
    coordinates, and pdfium reports nested bounds in that local space too; composing
    each enclosing form's matrix maps everything back onto the page."""
    stack = {0: _IDENT}
    for o in page.get_objects(max_depth=8):
        parent = stack.get(o.level, _IDENT)
        if o.type == C.FPDF_PAGEOBJ_FORM:
            stack[o.level + 1] = _compose(parent, _mat(o))
        yield o, parent


def _segment_points(obj, container=_IDENT) -> list[tuple[float, float]]:
    """Path segment vertices, transformed by the object matrix — and the enclosing form
    chain's — into page points."""
    a, b, c, d, e, f = _compose(container, _mat(obj))
    pts = []
    for i in range(C.FPDFPath_CountSegments(obj)):
        seg = C.FPDFPath_GetPathSegment(obj, i)
        x, y = ctypes.c_float(), ctypes.c_float()
        C.FPDFPathSegment_GetPoint(seg, ctypes.byref(x), ctypes.byref(y))
        pts.append((a * x.value + c * y.value + e,
                    b * x.value + d * y.value + f))
    return pts


def _polylines(page, region: tuple[float, float, float, float]) -> list[list[tuple[float, float]]]:
    x0, x1, y0, y1 = region
    out = []
    for obj, container in _walk(page):
        if obj.type != C.FPDF_PAGEOBJ_PATH:
            continue
        pts = _segment_points(obj, container)
        cx = sum(p[0] for p in pts) / len(pts) if pts else 0
        cy = sum(p[1] for p in pts) / len(pts) if pts else 0
        if x0 <= cx <= x1 and y0 <= cy <= y1:  # inside the figure's bbox
            out.append(pts)
    return out


def _is_rect(p: list[tuple[float, float]]) -> bool:
    return len(p) == 5 and len({round(x, 1) for x, _ in p}) == 2 and len({round(y, 1) for _, y in p}) == 2


def _fbox(p) -> tuple[float, float, float, float]:
    """A frame poly's bounds as (left, right, bottom, top) in page points."""
    return (min(x for x, _ in p), max(x for x, _ in p),
            min(y for _, y in p), max(y for _, y in p))


def _spine_frames(polys, region):
    """Frames assembled from separate spine strokes: matplotlib often draws an axes box
    as individual 2-point (or joined-L) segments rather than a rect path, and a figure
    saved without its background patch has no rect at all. A long horizontal stroke and a
    long vertical stroke sharing a bottom-left corner make a frame; the box closes off
    from their extents, so a spines-only (L-shaped) axes still frames."""
    x0, x1, y0, y1 = region
    rw, rh = x1 - x0, y1 - y0
    hs, vs = [], []
    for p in polys:
        for (ax, ay), (bx, by) in zip(p, p[1:]):
            if abs(ay - by) < 0.5 and abs(ax - bx) >= 0.08 * rw:
                hs.append((min(ax, bx), max(ax, bx), ay))
            elif abs(ax - bx) < 0.5 and abs(ay - by) >= 0.08 * rh:
                vs.append((min(ay, by), max(ay, by), ax))
    out = []
    for xl, xr, y in hs:
        for yb, yt, x in vs:
            if abs(x - xl) < 3 and abs(yb - y) < 3:  # shared bottom-left corner
                out.append([(x, y), (xr, y), (xr, yt), (x, yt), (x, y)])
    return out


def _axes_frames(polys, region):
    """Every candidate plot box in the region: axis-aligned rect paths plus frames
    assembled from spine strokes (_spine_frames), strictly inside the region (a
    full-figure border sitting on the region edge is excluded), big enough to hold a
    plot. A multi-panel figure yields one frame per subplot — and legend boxes and inset
    frames land here too; calibration is the filter (a legend has no numeric ticks around
    it, a readable inset does). Reading order (top-left first); a double-stroked frame or
    a rect patch coinciding with its spines collapses to one candidate."""
    x0, x1, y0, y1 = region
    rw, rh = x1 - x0, y1 - y0
    out = []
    for p in [q for q in polys if _is_rect(q)] + _spine_frames(polys, region):
        l, r, b, t = _fbox(p)
        if l <= x0 + 1 or b <= y0 + 1:
            continue
        if r - l < 0.04 * rw or t - b < 0.04 * rh:
            continue
        if any(abs(l - q[0]) < 2 and abs(r - q[1]) < 2 and abs(b - q[2]) < 2
               for q in (_fbox(o) for o in out)):
            continue
        out.append(p)
    out.sort(key=lambda p: (-_fbox(p)[3], _fbox(p)[0]))
    return out


def _assign(positions, frames):
    """Index of the smallest calibrated frame containing each position (None if none) —
    an inset's contents belong to the inset, not also to the panel drawn around it."""
    boxes = [_fbox(f) for f in frames]
    areas = [(r - l) * (t - b) for l, r, b, t in boxes]
    out = []
    for x, y in positions:
        best = None
        for i, (l, r, b, t) in enumerate(boxes):
            if l - 3 <= x <= r + 3 and b - 3 <= y <= t + 3 and (best is None or areas[i] < areas[best]):
                best = i
        out.append(best)
    return out


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
        if log_r2 > lin_r2 + 1e-6:
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
    return _Calibration(fx, fy, min(r2x, r2y), min(len(xt), len(yt)),
                        kx, ky, xflip or yflip or xflip2 or yflip2)


def _calibrate(page, frame, region):
    """Tier 1 calibration: tick values from the page's text (chars, plus object-level
    text for form-nested labels), fitted by _fit_ticks."""
    return _calibrate_groups(frame, _merged_text_groups(page, region))


def _calibrate_groups(frame, groups):
    ticks = [(v, mx, my) for g, mx, my in groups if (v := _token_value(g)) is not None]
    return _fit_ticks(frame, ticks)


def _data_series(polys, frame, fx, fy) -> list[list[tuple[float, float]]]:
    """Curves that span most of the plot width: the actual data traces, not markers
    (small) or gridlines (flat). Each vertex is mapped to data coordinates."""
    fw = max(x for x, _ in frame) - min(x for x, _ in frame)
    out = []
    for p in polys:
        if _is_rect(p):
            continue
        xext = max(x for x, _ in p) - min(x for x, _ in p)
        yext = max(y for _, y in p) - min(y for _, y in p)
        if xext < 0.5 * fw or yext < 1e-6:  # too short to be data, or a flat gridline
            continue
        out.append([(round(float(fx(x)), 4), round(float(fy(y)), 4)) for x, y in p])
    return out


def _marker_style(o) -> tuple:
    """A marker's appearance key: the stamped size plus the fill/stroke colors of the
    form's inner path. Same-series markers stamp identically; different series differ in
    glyph size or color, so grouping on this key separates the series."""
    l, b, r, t = o.get_pos()
    key = [round(r - l, 1), round(t - b, 1)]
    for i in range(C.FPDFFormObj_CountObjects(o)):
        child = C.FPDFFormObj_GetObject(o, i)
        for getter in (C.FPDFPageObj_GetFillColor, C.FPDFPageObj_GetStrokeColor):
            cr, cg, cb, ca = (ctypes.c_uint() for _ in range(4))
            if getter(child, *(ctypes.byref(v) for v in (cr, cg, cb, ca))):
                key += [cr.value, cg.value, cb.value, ca.value]
            else:
                key.append(None)
        break  # the first inner path carries the style; siblings repeat it
    return tuple(key)


def _page_forms(page, region):
    """All form XObjects in the region as (cx, cy, w, h, style key) in page space —
    collected once so each marker can be assigned to its panel, not double-counted by an
    enclosing one. A nested form's bounds are local to its container, so the corners ride
    through the composed transform."""
    x0, x1, y0, y1 = region
    out = []
    for o, container in _walk(page):
        if o.type != C.FPDF_PAGEOBJ_FORM:
            continue
        px0, px1, py0, py1 = _object_page_bounds(o, container)
        cx, cy = (px0 + px1) / 2, (py0 + py1) / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            out.append((cx, cy, px1 - px0, py1 - py0, _marker_style(o)))
    return out


def _object_page_bounds(obj, container):
    """Object bounds mapped through its enclosing form transform into page space."""
    left, bottom, right, top = obj.get_pos()
    a, b, c, d, e, f = container
    corners = [
        (a * x + c * y + e, b * x + d * y + f)
        for x, y in (
            (left, bottom),
            (right, bottom),
            (left, top),
            (right, top),
        )
    ]
    return (
        min(x for x, _ in corners),
        max(x for x, _ in corners),
        min(y for _, y in corners),
        max(y for _, y in corners),
    )


def _has_raster_image(page, region) -> bool:
    """Whether an embedded image materially overlaps the figure region."""
    x0, x1, y0, y1 = region
    region_area = max(1.0, (x1 - x0) * (y1 - y0))
    for obj, container in _walk(page):
        if obj.type != C.FPDF_PAGEOBJ_IMAGE:
            continue
        left, right, bottom, top = _object_page_bounds(obj, container)
        overlap = max(0.0, min(x1, right) - max(x0, left)) * max(
            0.0, min(y1, top) - max(y0, bottom)
        )
        if overlap / region_area >= 0.1:
            return True
    return False


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


_VLM_CONFIDENCE = 0.3  # a raster read is a rough estimate, not a measurement


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def _balanced_object(text: str) -> str | None:
    """The first balanced {...} in the text. A looping model repeats the whole object, and
    first-{ to last-} spans the loop as invalid JSON; the first balanced object is the read."""
    start = text.find("{")
    while start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        start = text.find("{", start + 1)
    return None


def _extract_json(text: str | None) -> dict | None:
    """The first JSON object in a model reply, tolerating a reasoning block, a code fence,
    stray prose around it, or the reply looping (the object repeated back-to-back)."""
    if not text:
        return None
    text = _THINK.sub("", text)  # drop a qwen3-style <think>...</think> reasoning block
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j <= i:
        return None
    for cand in (text[i:j + 1], _balanced_object(text)):
        if not cand:
            continue
        try:
            obj = json.loads(cand)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


_POINTS = re.compile(r'"points"\s*:\s*(\[\s*\[[^\]]*\](?:\s*,\s*\[[^\]]*\])*\s*\])')


def _points_arrays(text: str | None) -> list[list]:
    """Last-resort recovery: even a malformed reply (a missing quote on a key, mixed with
    a repetition loop — glm-ocr does both at once) usually carries clean "points" arrays.
    Pull those directly so one syntax slip doesn't zero the whole read; duplicate arrays
    (the loop repeating itself) collapse to one."""
    if not text:
        return []
    seen, out = set(), []
    for m in _POINTS.finditer(_THINK.sub("", text)):
        s = re.sub(r"\s", "", m.group(1))
        if s in seen:
            continue
        seen.add(s)
        try:
            out.append(json.loads(s))
        except ValueError:
            continue
    return out


def _coerce_points(raw) -> list[tuple[float, float]]:
    pts = []
    for p in raw if isinstance(raw, list) else []:
        try:
            pts.append((float(p[0]), float(p[1])))
        except (TypeError, ValueError, IndexError):
            continue
    return pts


def _in_range_fraction(series, cal) -> float:
    """Fraction of estimated points inside the pixel-calibrated axis ranges (10% slack
    each side). A model that read the right plot lands inside; hallucinated or wrong-axis
    values land out, so this only ever cuts the trust."""
    x0, x1 = sorted(cal.x_range)
    y0, y1 = sorted(cal.y_range)
    xpad = 0.1 * (x1 - x0)
    ypad = 0.1 * (y1 - y0)
    total = sum(len(s) for s in series)
    ok = sum(1 for s in series for x, y in s
             if x0 - xpad <= x <= x1 + xpad and y0 - ypad <= y <= y1 + ypad)
    return ok / total if total else 0.0


def vlm_digitize(crop_path: Path, describer, cal=None, *, cache: dict | None = None,
                 endpoint: str = "", max_tokens: int | None = None,
                 vote: int = 0, temperature: float | None = None) -> Digitization | None:
    """Tier 2: estimate a raster plot's data with a vision model, for figures tier 1 (the
    vector reader) can't touch. Approximate and never lossless -- the crop stays
    authoritative. With a pixel calibration (calibrate.analyze_raster) the read is
    ANCHORED: the measured axis ranges ride in the prompt, points outside them cut the
    trust, and only a well-calibrated in-range read earns enough confidence to print its
    numbers (further scaled by the pixel round-trip in the pipeline). Unanchored reads
    keep the flat low confidence, so their numbers stay withheld.

    `vote`/`temperature` serve the consensus wrapper: vote 0 rides the endpoint default
    so a single read stays byte-identical; extra votes sample at `temperature` and get
    their own cache keys."""
    context = ""
    if cal is not None:
        context = (f"Axis ranges measured from the pixels: x runs {cal.x_range[0]:g} to "
                   f"{cal.x_range[1]:g} ({cal.x_kind}), y runs {cal.y_range[0]:g} to "
                   f"{cal.y_range[1]:g} ({cal.y_kind}). Read every point against these "
                   "ranges; do not return points outside them.")
    key = ""
    if cache is not None:
        from pdf2md.describe import vision_cache_key

        key = vision_cache_key(
            Path(crop_path), describer, "digitize", context=context,
            max_tokens=max_tokens, endpoint=endpoint,
        ) + (f":vote-{vote}" if vote else "")
    raw = cache.get(key) if cache is not None else None
    if raw is None:
        kwargs: dict = {"max_tokens": max_tokens}
        if temperature is not None:
            kwargs["temperature"] = temperature
        raw = describer.describe(Path(crop_path), "digitize", context=context, **kwargs)
        if raw and cache is not None:
            cache[key] = raw
    data = _extract_json(raw)
    raw_series = data.get("series", []) if data else []
    series = [pts for s in (raw_series if isinstance(raw_series, list) else [])
              if isinstance(s, dict) and (pts := _coerce_points(s.get("points")))]
    salvaged = False
    if not series:  # malformed/looping JSON: salvage the points arrays themselves
        series = [pts for arr in _points_arrays(raw) if (pts := _coerce_points(arr))]
        salvaged = bool(series)
    if not series:
        return None
    n = sum(len(s) for s in series)
    tail = "; recovered from a malformed model reply" if salvaged else ""
    if cal is None:
        note = (f"VLM-estimated from the raster image, {len(series)} series / {n} points — "
                f"approximate, verify against the image{tail}")
        return Digitization(series, "vlm-estimated", _VLM_CONFIDENCE, note)
    inside = _in_range_fraction(series, cal)
    confidence = round((0.35 + 0.35 * cal.confidence) * inside, 2)
    note = (f"VLM read anchored to pixel-calibrated axes "
            f"(x {cal.x_range[0]:g} to {cal.x_range[1]:g} {cal.x_kind}, "
            f"y {cal.y_range[0]:g} to {cal.y_range[1]:g} {cal.y_kind}), "
            f"{len(series)} series / {n} points — an estimate, verify against the image{tail}")
    if inside < 0.9:
        note += f"; {100 - round(inside * 100)}% of points fall outside the calibrated ranges"
    return Digitization(series, "vlm-anchored", confidence, note,
                        x_kind=cal.x_kind, y_kind=cal.y_kind)


# Consensus sampling for raster reads (the 2026 self-ensembling result): a single
# VLM decode is one draw from a noisy distribution; N draws aggregated per-bin
# by median land closer to the truth, and the dispersion across draws is itself
# the uncertainty signal. Only x-functional curves (one y per x — lines and bar
# series) aggregate; scatter-like point sets fall back to the best single read.

_CONSENSUS_BINS = 48
_CONVERGED_AT = 0.02   # mean per-bin MAD below this fraction of the y-range
_MIN_VOTES_BEFORE_STOP = 3


def _functional(points: list[tuple[float, float]]) -> bool:
    """One y per x (no repeated x): interpolable as a curve."""
    xs = sorted(x for x, _ in points)
    span = (xs[-1] - xs[0]) or 1.0
    return all(b - a > 1e-9 * span for a, b in zip(xs, xs[1:]))


def _median_curves(samples: list[list[list[tuple[float, float]]]],
                   shared_domain: tuple[float, float] | None = None,
                   ) -> tuple[list[list[tuple[float, float]]], float] | None:
    """Per-series binned median across samples, in data coordinates, plus the mean
    per-bin dispersion as a fraction of the union y-range. All samples are resampled
    onto ONE shared x-domain — `shared_domain` (the calibrated axis range) when
    given, else the samples' common intersection — so bin k means the same x in
    every draw; per-sample domains would silently misalign the median. None when
    series counts disagree past majority alignment, any read is non-functional
    (scatter-like), or the domains don't intersect."""
    counts = Counter(len(s) for s in samples)
    n_series, n_with = counts.most_common(1)[0]
    if n_with < max(2, len(samples) // 2 + len(samples) % 2):
        return None
    usable = [s for s in samples if len(s) == n_series]
    if not all(_functional(ser) for s in usable for ser in s):
        return None
    los, his = [], []
    for s in usable:
        for ser in s:
            xs = [p[0] for p in ser]
            if not xs:
                return None
            los.append(min(xs))
            his.append(max(xs))
    if shared_domain is not None:
        lo, hi = sorted(shared_domain)
        lo = max(lo, max(los))
        hi = min(hi, max(his))
    else:
        lo, hi = max(los), min(his)
    if not usable or hi <= lo:
        return None
    y_lo = min(p[1] for s in usable for ser in s for p in ser)
    y_hi = max(p[1] for s in usable for ser in s for p in ser)
    y_span = (y_hi - y_lo) or 1.0
    out: list[list[tuple[float, float]]] = []
    spread: list[float] = []
    # Every sample covers the shared domain by construction (it's the calibrated
    # range clamped to the reads' intersection), so bin k is the same x everywhere.
    grid_x = np.linspace(lo, hi, _CONSENSUS_BINS)
    for idx in range(n_series):
        stacked_rows = []
        for sample in usable:
            pts = sorted(sample[idx])
            if not pts:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            stacked_rows.append(np.interp(grid_x, xs, ys))
        if not stacked_rows:
            continue
        stacked = np.stack(stacked_rows)
        median_y = np.median(stacked, axis=0)
        # dispersion: mean absolute deviation from the median, per bin, then over
        # bins and series, normalized by the union y-range.
        spread.append(float(np.abs(stacked - median_y).mean()) / y_span)
        step = max(1, _CONSENSUS_BINS // 32)
        out.append([(float(x), float(y))
                    for x, y in zip(grid_x[::step], median_y[::step])])
    if not out:
        return None
    return out, float(np.mean(spread)) if spread else 0.0


def vlm_digitize_consensus(crop_path: Path, describer, cal=None, *, votes: int = 1,
                           temperature: float = 0.4, cache: dict | None = None,
                           endpoint: str = "", max_tokens: int | None = None,
                          ) -> Digitization | None:
    """`vlm_digitize` sampled `votes` times and aggregated: per-bin median curves,
    convergence early-stop once the draws agree, and the across-draw dispersion
    carried on the result to scale confidence. A draw set that isn't x-functional
    (scatter-like) or can't align falls back to the highest-confidence single read,
    flagged in its note."""
    reads: list[Digitization | None] = []
    for i in range(max(1, votes)):
        reads.append(vlm_digitize(crop_path, describer, cal, cache=cache,
                                  endpoint=endpoint, max_tokens=max_tokens,
                                  vote=i, temperature=None if i == 0 else temperature))
        good_now = [d for d in reads if d is not None and d.series]
        # Early stop once the draws agree: more votes would buy nothing.
        if i + 1 >= max(2, _MIN_VOTES_BEFORE_STOP) and len(good_now) == i + 1:
            converged = _median_curves([d.series for d in good_now],
                                       shared_domain=cal.x_range if cal else None)
            if converged is not None and converged[1] < _CONVERGED_AT:
                break
    good = [d for d in reads if d is not None and d.series]
    if not good:
        return next((d for d in reads if d is not None), None)

    def note_for(base_note: str, used: int, disp: float | None) -> str:
        tail = (f"; {used}-vote median consensus" +
                (f", dispersion {disp * 100:.1f}% of y-range" if disp is not None else ""))
        return base_note + tail

    if len(good) == 1:
        base = good[0]
        return Digitization(base.series, base.method, base.confidence,
                            note_for(base.note, 1, None),
                            x_kind=base.x_kind, y_kind=base.y_kind,
                            consensus_votes=1)
    aggregated = _median_curves([d.series for d in good],
                                shared_domain=cal.x_range if cal else None)
    confidence = sorted(d.confidence for d in good)[len(good) // 2]  # median read
    if aggregated is None:
        best = max(good, key=lambda d: d.confidence)
        return Digitization(best.series, best.method, confidence,
                            note_for(best.note + "; point sets did not align",
                                     len(good), None),
                        x_kind=best.x_kind, y_kind=best.y_kind,
                        consensus_votes=len(good))
    series, dispersion = aggregated
    scaled = round(confidence * (1.0 - min(0.9, dispersion * 3.0)), 2)
    base = good[0]
    return Digitization(series, "vlm-consensus", scaled,
                        note_for(base.note, len(good), dispersion),
                        x_kind=base.x_kind, y_kind=base.y_kind,
                        consensus_votes=len(good), dispersion=round(dispersion, 4))



def _render_estimate(series, size=(320, 320)):
    """Draw the estimated series as a minimal plot (PIL, no matplotlib, no code execution)
    for a visual round-trip check: same data range, curves as polylines with point dots."""
    from PIL import Image, ImageDraw

    pts = [p for s in series for p in s]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    xspan = (xmax - xmin) or 1.0
    yspan = (ymax - ymin) or 1.0
    m, (w, h) = 28, size

    def px(x, y):
        return (m + (x - xmin) / xspan * (w - 2 * m),
                h - m - (y - ymin) / yspan * (h - 2 * m))  # flip y into image space

    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    d.rectangle([m, m, w - m, h - m], outline="black")
    for s in series:
        line = [px(x, y) for x, y in s]
        if len(line) > 1:
            d.line(line, fill="black", width=1)
        for a, b in line:
            d.ellipse([a - 2, b - 2, a + 2, b + 2], fill="black")
    return img


def _composite(crop_path, recon):
    from PIL import Image

    orig = Image.open(crop_path).convert("RGB")
    h = recon.height
    orig = orig.resize((max(1, round(orig.width * h / orig.height)), h))
    out = Image.new("RGB", (orig.width + recon.width + 12, h), "white")
    out.paste(orig, (0, 0))
    out.paste(recon, (orig.width + 12, 0))
    return out


def write_estimate_composite(crop_path: Path, series, out_path: Path) -> None:
    """Save the original beside a reconstruction of the estimate so a human can eyeball the
    round-trip. The trust score comes from `pixel_fit`, not from this image."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _composite(crop_path, _render_estimate(series)).save(out_path)


_FIT_TOL = 0.06  # a point within ~6% of the plot box of drawn ink counts as sitting on a curve


def pixel_fit(crop_path: Path, series) -> float:
    """Round-trip agreement measured from the pixels, not graded by a model: the mean
    (normalized) distance from each estimated point to the nearest dark 'ink' pixel in the
    crop, mapped to 0..1. A curve that tracks the drawn data lands on ink (score near 1); one
    the model hallucinated in whitespace lands far from any ink (near 0). Both sides are
    normalized to their bounding box, so this checks the estimate's SHAPE against the drawn
    curves -- the VLM already read the axis magnitudes. It's a rough sanity signal, not a
    precise accuracy score: axis lines, ticks, and labels inside the crop are ink too, and a
    rotated or heavily-cropped plot won't align. Errors / no ink -> 0.0."""
    from PIL import Image

    try:
        img = np.asarray(Image.open(crop_path).convert("L"))
    except Exception:  # noqa: BLE001 - a crop we can't open scores 0, like any failed check
        return 0.0
    ys, xs = np.where(img < 128)  # dark pixels: row (y, down) and col (x)
    pts = [p for s in series for p in s]
    if len(xs) < 10 or len(pts) < 2:
        return 0.0
    # normalize ink to its bounding box, robustly (percentiles shrug off a stray label pixel)
    x0, x1 = np.percentile(xs, [1, 99])
    y0, y1 = np.percentile(ys, [1, 99])
    ink = np.column_stack(((xs - x0) / ((x1 - x0) or 1.0), (ys - y0) / ((y1 - y0) or 1.0)))
    if len(ink) > 4000:  # cap the cloud so the nearest-point scan stays cheap
        ink = ink[np.linspace(0, len(ink) - 1, 4000).astype(int)]
    px = np.array([p[0] for p in pts], float)
    py = np.array([p[1] for p in pts], float)
    qx = (px - px.min()) / ((px.max() - px.min()) or 1.0)
    qy = 1.0 - (py - py.min()) / ((py.max() - py.min()) or 1.0)  # data y is up; flip to image y-down
    q = np.column_stack((qx, qy))
    d = np.sqrt(((q[:, None, :] - ink[None, :, :]) ** 2).sum(-1)).min(1)  # nearest ink per point
    return round(float(np.clip(1.0 - d.mean() / _FIT_TOL, 0.0, 1.0)), 3)


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


def _panel_series(pg, panels, polys, forms):
    """Extract each calibrated panel's series. Returns (series, per-series names,
    kinds, per-panel confidences, r2s, skipped); a panel whose own calibration
    confidence is below _PANEL_FLOOR is skipped (counted in `skipped`) so one misread
    inset can't drag every other panel's good data under the emit floor. Names are
    None-worthy only by the caller (single-panel figures don't need them)."""
    frames = [fr for fr, _ in panels]
    poly_of = _assign([(sum(x for x, _ in p) / len(p), sum(y for _, y in p) / len(p))
                       for p in polys], frames)
    form_of = _assign([(f[0], f[1]) for f in forms], frames)
    out_series, names, kinds, confs, r2s = [], [], [], [], []
    used = skipped = 0
    for i, (frame, cal) in enumerate(panels):
        confidence = round(max(0.0, min(1.0, cal.r2)) * min(1.0, cal.nticks / 3), 3)
        if cal.flipped:  # signs inferred from monotonicity: usually right, but an inverted axis
            confidence = round(confidence * 0.7, 3)  # can't self-check, so hedge (don't cripple)
        if len(panels) > 1 and confidence < _PANEL_FLOOR:
            skipped += 1
            continue
        mine = [p for p, w in zip(polys, poly_of) if w == i]
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
        used += 1
        confs.append(confidence)
        r2s.append(cal.r2)
        kinds.append(kind)
        # a panel whose axis scale differs from the figure's first panel says so in its
        # series names, since Digitization carries one x_kind/y_kind pair
        scale = ("" if (cal.x_kind, cal.y_kind) == (panels[0][1].x_kind, panels[0][1].y_kind)
                 else f" ({cal.x_kind} x, {cal.y_kind} y)")
        names += [f"panel {used} series {j}{scale}" for j in range(1, len(series) + 1)]
        out_series += series
    return out_series, names, kinds, confs, r2s, skipped


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
    series, names, kinds, confs, r2s, skipped = _panel_series(page, panels, polys, forms)
    if not series:
        return None
    confidence = round(min(confs) * 0.9, 3)  # OCR-read ticks: haircut vs the text layer
    if confidence < _PANEL_FLOOR:
        return None  # not printable; leave the figure to the crop (or the VLM tier)
    npts = sum(len(s) for s in series)
    multi = len(confs) > 1
    note = ("vector curve paths with OCR-read axes (tick text is outlined to paths; "
            "axis numbers read off the rendered crop), "
            + (f"{len(confs)} panels, " if multi else "")
            + f"{len(series)} series / {npts} points (min fit R^2={min(r2s):.3f})"
            + (f"; {skipped} weakly-calibrated panel(s) skipped" if skipped else "")
            + " — verify the axis ranges against the image")
    return Digitization(series, "vector-path/ocr-axes", confidence, note,
                        x_kind=panels[0][1].x_kind, y_kind=panels[0][1].y_kind,
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
        series, names, kinds, confs, r2s, skipped = _panel_series(page, panels, polys, forms)
        if not series:
            return None, geometry
        cal0 = panels[0][1]
        confidence = min(confs)
        kind = _dominant_kind(kinds)
        npts = sum(len(s) for s in series)
        if len(confs) == 1:  # single panel: the common case, same note as always
            what = (f"{len(series)} series" if kind == "line"
                    else f"{len(series)} series / {npts} {kind} points" if len(series) > 1
                    else f"{npts} {kind} points")
            axes = cal0.x_kind if cal0.x_kind == cal0.y_kind else f"{cal0.x_kind}/{cal0.y_kind}"
            note = (f"vector paths, {what}; {axes} axes calibrated on {cal0.nticks}+ ticks/axis "
                    f"(fit R^2={cal0.r2:.3f}{'; signs inferred' if cal0.flipped else ''})")
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
