"""Shapes read off a PDF page: the drawn paths, the frames they sit in, and the
stamped marker forms.

Split out of `digitize.py` so the dependency runs one way. Everything here is
geometry with no notion of what a coordinate means -- no ticks, no axis values,
no confidence. `digitize.py` maps these shapes to data and judges the mapping;
this module only finds them. That boundary is where every figure defect found so
far has lived, which is the argument for drawing it.

pypdfium2 is the only PDF dependency, the engine already in use.
"""

from __future__ import annotations

import ctypes

import pypdfium2.raw as C


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


_BEZIER_STEPS = 8  # points emitted along each cubic; a curve read at its control
                   # points can miss the curve entirely, at 8 it is visually exact


def _segment_points(obj, container=_IDENT) -> list[tuple[float, float]]:
    """Path vertices in page points, with cubic Beziers flattened onto the curve.

    pdfium reports a cubic as three BEZIERTO segments -- two control points and the
    endpoint -- and taking all three as data puts the control points, which are not
    on the curve, into the series. On a curve drawn as a few long Beziers that is the
    whole reading: Atkins Fig. 3.4 is `ln(Vf/Vi)` and came back with (2.95, 1.86)
    where the printed curve passes through (2.95, 1.08). A curve drawn as many short
    Beziers hides the error, which is why it did not show up in the aggregate.

    Transformed by the object matrix and the enclosing form chain's."""
    a, b, c, d, e, f = _compose(container, _mat(obj))

    def page_point(x, y):
        return (a * x + c * y + e, b * x + d * y + f)

    raw = []
    for i in range(C.FPDFPath_CountSegments(obj)):
        seg = C.FPDFPath_GetPathSegment(obj, i)
        x, y = ctypes.c_float(), ctypes.c_float()
        C.FPDFPathSegment_GetPoint(seg, ctypes.byref(x), ctypes.byref(y))
        raw.append((C.FPDFPathSegment_GetType(seg), x.value, y.value))

    pts: list[tuple[float, float]] = []
    i = 0
    while i < len(raw):
        kind, x, y = raw[i]
        if (kind == C.FPDF_SEGMENT_BEZIERTO and i + 2 < len(raw)
                and raw[i + 1][0] == C.FPDF_SEGMENT_BEZIERTO
                and raw[i + 2][0] == C.FPDF_SEGMENT_BEZIERTO and pts):
            (x0, y0) = pts[-1]
            (c1x, c1y), (c2x, c2y) = page_point(x, y), page_point(raw[i + 1][1], raw[i + 1][2])
            (x3, y3) = page_point(raw[i + 2][1], raw[i + 2][2])
            for step in range(1, _BEZIER_STEPS + 1):
                t = step / _BEZIER_STEPS
                u = 1.0 - t
                pts.append((
                    u * u * u * x0 + 3 * u * u * t * c1x + 3 * u * t * t * c2x + t * t * t * x3,
                    u * u * u * y0 + 3 * u * u * t * c1y + 3 * u * t * t * c2y + t * t * t * y3,
                ))
            i += 3
            continue
        pts.append(page_point(x, y))
        i += 1
    return pts


def stroke_colour(obj) -> tuple[int, int, int] | None:
    """A path's stroke colour, or None when pdfium will not say.

    A chart with two y axes is drawn so a reader can tell which curve belongs to
    which scale, and colour is how: on Atkins Fig. 5.1 the left ticks, the word
    "Water" and the water curve are all (0, 102, 165), while the right ticks,
    "Ethanol" and its curve are all (113, 45, 125). That is the assignment, stated
    by the document itself."""
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    if not C.FPDFPageObj_GetStrokeColor(obj.raw if hasattr(obj, "raw") else obj, r, g, b, a):
        return None
    return (r.value, g.value, b.value)


def fill_colour(obj) -> tuple[int, int, int] | None:
    """The fill colour of a text object -- what a tick label is drawn in."""
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    if not C.FPDFPageObj_GetFillColor(obj.raw if hasattr(obj, "raw") else obj, r, g, b, a):
        return None
    return (r.value, g.value, b.value)


def coloured_polylines(page, region: tuple[float, float, float, float]):
    """`(points, stroke colour)` for every path whose centre is inside `region`.

    `_polylines` is this without the colours; both walk in one order so a caller
    that wants both gets them aligned by construction rather than by index."""
    x0, x1, y0, y1 = region
    out = []
    for obj, container in _walk(page):
        if obj.type != C.FPDF_PAGEOBJ_PATH:
            continue
        pts = _segment_points(obj, container)
        cx = sum(p[0] for p in pts) / len(pts) if pts else 0
        cy = sum(p[1] for p in pts) / len(pts) if pts else 0
        if x0 <= cx <= x1 and y0 <= cy <= y1:  # inside the figure's bbox
            out.append((pts, stroke_colour(obj)))
    return out


def _polylines(page, region: tuple[float, float, float, float]) -> list[list[tuple[float, float]]]:
    return [pts for pts, _ in coloured_polylines(page, region)]


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
