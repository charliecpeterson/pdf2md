"""Model-free pre-scan of a raster (scanned) chart crop: axis calibration from the
pixels + OCR'd tick numbers, and an ambiguity measure of the plot interior. Feeds the
VLM digitization tier — a calibration anchors the model's read, and a tangled chart
(many overlapping ink traces) is vetoed before the model can invent a data table.
Shares the tick-fit machinery with the vector tier (digitize.py); see
docs/figure-to-text.md for the method and measured limits this implements."""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import numpy as np

from pdf2md.digitize import fit_axis, restore_signs
from pdf2md.labels import best_orientation

_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_SCI = re.compile(r"[+-]?\d+(?:\.\d+)?[eE][+-]?\d+")
_POW10 = re.compile(r"10(-\d+)")  # a printed 10^-n tick OCRs flattened: "10-2"


def tick_value(token: str) -> float | None:
    """Numeric value of an OCR'd tick token. Beyond plain numbers: a log tick's
    superscript flattens in OCR ("10-2" is 10^-2 — unambiguous with the minus present;
    a flattened POSITIVE exponent like "103" is indistinguishable from the literal and
    stays unparsed), and scientific notation ("1e-05") parses directly."""
    t = token.strip().replace("−", "-")
    if _NUM.fullmatch(t):
        return float(t)
    if _SCI.fullmatch(t):
        return float(t)
    m = _POW10.fullmatch(t)
    if m:
        return 10.0 ** int(m.group(1))
    return None

# Above this many distinct ink traces per interior column, no automated read (classical
# or VLM) can be trusted to separate the curves — withhold instead of estimating. A clean
# 1-2 curve scan measures 1-3; the pathological overlapping-waterfall case measures 8+.
AMBIGUITY_MAX = 4.0


class RasterCalibration(NamedTuple):
    x_range: tuple[float, float]  # data values at the frame's left/right edges
    y_range: tuple[float, float]  # data values at the frame's bottom/top edges
    x_kind: str                   # "linear" | "log"
    y_kind: str
    r2: float
    nticks: int
    confidence: float


class RasterAnalysis(NamedTuple):
    calibration: RasterCalibration | None
    ambiguity: float  # median distinct ink runs per plot-interior column
    angle: int        # 90-degree rotation corrected before reading
    skew: float       # fine deskew applied (degrees)


def _ink_threshold(gray: np.ndarray) -> int:
    """Ink/paper split for a document scan: paper is the histogram's dominant mode, ink
    is anything well below the mode's own spread (4 sigma, floor 16 levels). Plain Otsu
    fails here the classic way — ink is a tiny class, so on a noisy scan the 'optimal'
    split lands inside the paper noise and half the page reads as ink. A dark-background
    image (mode < 128) falls back to Otsu."""
    hist = np.bincount(gray.ravel(), minlength=256)
    mode = int(hist.argmax())
    if mode < 128:
        return _otsu(gray)
    paper = gray[np.abs(gray.astype(int) - mode) <= 32]
    sigma = float(paper.std())
    return max(1, int(mode - max(4.0 * sigma, 16.0)))


def _otsu(gray: np.ndarray) -> int:
    """Otsu threshold; ink is `gray <= _otsu(gray)`. (<=, not <: on a noise-free two-tone
    image every threshold ties and argmax returns the ink bin itself.)"""
    hist = np.bincount(gray.ravel(), minlength=256).astype(float)
    w = hist.cumsum()
    m = (np.arange(256) * hist).cumsum()
    valid = (w > 0) & (w < w[-1])
    if not valid.any():  # flat image: no ink/paper split to find
        return 0
    between = np.zeros(256)
    between[valid] = (m[-1] * w[valid] - m[valid]) ** 2 / (w[valid] * (w[-1] - w[valid]))
    return int(between.argmax())


def _deskew_measure(dark: np.ndarray) -> tuple[float, float]:
    """Fine skew of a scan, from the axes: the angle that sharpens the row/column
    projections most is the one that squares up the longest straight lines. Returns
    the correction angle and its fractional sharpness gain over leaving the image
    unchanged."""
    from PIL import Image

    small = Image.fromarray((dark * 255).astype(np.uint8))
    small.thumbnail((400, 400))
    scores = []
    for deg in np.arange(-3.0, 3.01, 0.5):
        arr = np.asarray(small.rotate(deg, fillcolor=0)) > 127
        sharp = float((arr.mean(0) ** 2).sum() + (arr.mean(1) ** 2).sum())
        scores.append((float(deg), sharp))
    best_deg, best_sharp = max(scores, key=lambda item: item[1])
    upright_sharp = next(sharp for deg, sharp in scores if deg == 0.0)
    gain = (best_sharp - upright_sharp) / upright_sharp if upright_sharp else 0.0
    return best_deg, gain


def _deskew(dark: np.ndarray) -> float:
    """Correction angle used by the chart pre-scan."""
    return _deskew_measure(dark)[0]


def scan_deskew_angle(gray: np.ndarray) -> float:
    """Return a conservative fine-deskew correction for a document raster.

    Refuse small, weak, and boundary detections. A best score at the search boundary
    means the real angle may lie outside the range where this correction is safe.
    """
    dark = gray <= _ink_threshold(gray)
    angle, gain = _deskew_measure(dark)
    if abs(angle) < 0.75 or abs(angle) >= 3.0 or gain < 0.05:
        return 0.0
    return angle


def _lines(frac: np.ndarray, thr: float = 0.45) -> list[int]:
    """Positions of long straight lines: indices whose dark fraction clears `thr`,
    grouped so a 2-3px-thick stroke reads as one line."""
    idx = np.where(frac >= thr)[0]
    out: list[list[int]] = []
    for i in idx:
        if out and i - out[-1][-1] <= 3:
            out[-1].append(int(i))
        else:
            out.append([int(i)])
    return [int(np.mean(g)) for g in out]


def _frame(dark: np.ndarray) -> tuple[int, int, int, int] | None:
    """The plot box (left, right, top, bottom) in pixels. Needs a y-axis line in the left
    half and an x-axis line in the bottom half; a missing right/top side (an L-shaped
    frame) is closed off from the axis lines' own extents."""
    h, w = dark.shape
    vs = _lines(dark.mean(0))
    hs = _lines(dark.mean(1))
    if not vs or not hs:
        return None
    left, bottom = min(vs), max(hs)
    if left > 0.5 * w or bottom < 0.5 * h:
        return None
    right = max(vs) if max(vs) - left > 0.3 * w else None
    if right is None:
        band = dark[max(0, bottom - 2):bottom + 3, :].any(0)
        right = int(np.where(band)[0].max()) if band.any() else w - 1
    top = min(hs) if bottom - min(hs) > 0.3 * h else None
    if top is None:
        band = dark[:, max(0, left - 2):left + 3].any(1)
        top = int(np.where(band)[0].min()) if band.any() else 0
    if right - left < 0.2 * w or bottom - top < 0.2 * h:
        return None
    return left, right, top, bottom


def _calibrate(frame, nums, shape) -> RasterCalibration | None:
    """Pin the frame to data coordinates from the numeric tokens sitting below the x-axis
    and left of the y-axis — the same value-vs-position fit as the vector tier, so log
    axes and dropped minus signs get the same treatment. OCR'd ticks are a model read,
    so the confidence is haircut relative to the vector tier's."""
    left, right, top, bottom = frame
    h, w = shape
    xt = sorted(((v, cx) for v, cx, cy in nums
                 if bottom + 2 < cy < bottom + 0.25 * h
                 and left - 0.05 * w <= cx <= right + 0.05 * w), key=lambda t: t[1])
    yt = sorted(((v, cy) for v, cx, cy in nums
                 if left - 0.25 * w < cx < left - 2
                 and top - 0.05 * h <= cy <= bottom + 0.05 * h), key=lambda t: t[1])
    if len(xt) < 2 or len(yt) < 2:
        return None
    xt, xflip = restore_signs(xt)
    yt, yflip = restore_signs(yt)
    fx, r2x, kx = fit_axis(xt)
    fy, r2y, ky = fit_axis(yt)
    r2 = min(r2x, r2y)
    n = min(len(xt), len(yt))
    conf = max(0.0, min(1.0, r2)) * min(1.0, n / 3) * 0.9
    if xflip or yflip:
        conf *= 0.7
    return RasterCalibration((fx(left), fx(right)), (fy(bottom), fy(top)),
                             kx, ky, r2, n, round(conf, 3))


def _ambiguity(dark: np.ndarray, frame, boxes) -> float:
    """Median count of distinct ink runs per column of the plot interior — how many
    separate traces an automated read would have to keep apart. Text inside the plot
    (masked via the OCR boxes) and full-width/height gridlines aren't traces."""
    h, w = dark.shape
    left, right, top, bottom = frame if frame else (
        int(0.15 * w), int(0.85 * w), int(0.15 * h), int(0.85 * h))
    mx, my = max(3, int(0.03 * (right - left))), max(3, int(0.03 * (bottom - top)))
    inner = dark[top + my:bottom - my, left + mx:right - mx].copy()
    if inner.size == 0:
        return 0.0
    ox, oy = left + mx, top + my
    for box in boxes:
        x0 = max(0, int(min(p[0] for p in box)) - ox)
        x1 = max(0, int(max(p[0] for p in box)) - ox)
        y0 = max(0, int(min(p[1] for p in box)) - oy)
        y1 = max(0, int(max(p[1] for p in box)) - oy)
        inner[y0:y1 + 1, x0:x1 + 1] = False
    inner[inner.mean(1) > 0.7, :] = False
    inner[:, inner.mean(0) > 0.7] = False
    runs = (inner[1:, :] & ~inner[:-1, :]).sum(0) + inner[0, :]
    runs = runs[runs > 0]
    return float(np.median(runs)) if runs.size else 0.0


def analyze_raster(crop_path: Path, reader, assume_upright: bool = False) -> RasterAnalysis | None:
    """Pre-scan a chart crop: correct orientation (coarse 90-degree via OCR legibility,
    fine skew via the axis lines), then measure calibration and ambiguity. `reader` is
    the shared RapidOCR instance (None degrades to ambiguity-only: no tick numbers, no
    rotation correction). `assume_upright` skips the 90-degree hunt — right for a render
    of a born-digital page, where a rotated y-axis title otherwise fools the legibility
    vote. Returns None only when the image can't be opened."""
    from PIL import Image

    try:
        img = Image.open(crop_path).convert("L")
    except Exception:  # noqa: BLE001 - an unreadable crop just isn't analyzable
        return None
    angle = 0
    if reader is not None and not assume_upright:
        _, angle = best_orientation(reader, img.convert("RGB"))
        if angle:
            img = img.rotate(-angle, expand=True)
    gray = np.asarray(img)
    dark = gray <= _ink_threshold(gray)
    skew = _deskew(dark)
    if abs(skew) >= 0.5:
        img = img.rotate(skew, fillcolor=255)
        gray = np.asarray(img)
        dark = gray <= _ink_threshold(gray)
    toks = []
    if reader is not None:
        res = reader(np.asarray(img.convert("RGB")))
        if res is not None and getattr(res, "txts", None):
            toks = list(zip(res.txts, res.scores, res.boxes))
    nums = []
    for t, s, box in toks:
        if s >= 0.5 and (v := tick_value(t)) is not None:
            cx = float(np.mean([p[0] for p in box]))
            cy = float(np.mean([p[1] for p in box]))
            nums.append((v, cx, cy))
    frame = _frame(dark)
    cal = _calibrate(frame, nums, dark.shape) if frame else None
    amb = _ambiguity(dark, frame, [b for _, _, b in toks])
    return RasterAnalysis(cal, round(amb, 1), angle, skew)
