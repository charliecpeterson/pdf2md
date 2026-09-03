"""Tier 2 of the figure ladder: estimate a raster plot's data with a vision model.

Split out of `digitize.py`, which had grown to 1,318 lines around three
independent concerns. This one shares nothing with the vector tier but the
`Digitization` type it returns: a different input (a rendered crop, not the PDF's
drawn paths), a different failure mode (a model that hallucinates rather than a
mapping that misfits), and its own consensus and round-trip verification. It is
approximate by construction -- the crop stays authoritative -- where the vector
tier is near-lossless.

`calibrate.analyze_raster` supplies the pixel-measured axis ranges that anchor a
read; without them a reply is kept at low confidence and its numbers withheld.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import numpy as np

from pdf2md.schema import Digitization


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

