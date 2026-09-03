"""Measure the raster-chart pre-scan (calibrate.py) — and optionally anchored VLM
digitization — against synthetic scanned charts with known truth.

    uv run python scripts/eval_raster.py                          # pre-scan only, model-free
    uv run --extra describe python scripts/eval_raster.py glm-ocr:q8_0   # + live VLM (ollama)

Like eval_digitize.py there are no hand labels: we generate scan-like charts (PIL: frame,
ticks, curves, then rotation/blur/noise) whose data we know, and score what the pre-scan
recovers. Each case probes one condition: clean, noisy (the Otsu failure mode), rotated
(skew detection), crossing curves (must pass the gate), tangled curves (must BE gated),
and no axes (must decline calibration). The load-bearing checks are the gate verdicts and
that calibration error stays low wherever calibration is claimed. With a model argument,
the non-gated cases also run vlm_digitize anchored to the recovered calibration and report
point error + whether the final confidence would print the numbers. Deterministic (seeded
noise); the VLM half is only as deterministic as the endpoint."""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from pdf2md.calibrate import AMBIGUITY_MAX, analyze_raster
from pdf2md.digitize_vlm import pixel_fit, vlm_digitize
from pdf2md.labels import load_figure_ocr

W, H = 840, 640
L, R, T, B = 100, 740, 60, 560  # frame; x maps 0..10, y maps 0..100


def draw_chart(path, curves, frame=True, ticks=True, rotate=0.0, blur=0.0, noise=0.0):
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=22)
    if frame:
        d.rectangle([L, T, R, B], outline=0, width=3)
    if ticks:
        for v in range(0, 11, 2):
            x = L + v / 10 * (R - L)
            d.line([x, B, x, B + 10], fill=0, width=3)
            d.text((x - 10, B + 16), str(v), fill=0, font=font)
        for v in range(0, 101, 20):
            y = B - v / 100 * (B - T)
            d.line([L - 10, y, L, y], fill=0, width=3)
            d.text((L - 55, y - 12), str(v), fill=0, font=font)
    for c in curves:
        pts = [(L + u / 100 * (R - L), B - max(0.0, min(100.0, c(u / 10))) / 100 * (B - T))
               for u in range(101)]
        d.line(pts, fill=0, width=4)
    if rotate:
        img = img.rotate(rotate, expand=True, fillcolor=255)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    if noise:
        arr = np.asarray(img).astype(float)
        arr += np.random.default_rng(7).normal(0, noise, arr.shape)
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    img.save(path)


def _exp(x):
    return 100.0 * math.exp(-x / 4.0)


CASES = [
    # name, curves (data units: x 0..10 -> y 0..100), kwargs, should_gate, expects_cal
    ("clean single curve", [_exp], {}, False, True),
    ("noisy scan", [_exp], {"rotate": 1.5, "blur": 0.6, "noise": 6}, False, True),
    ("crossing pair", [lambda x: 10 * x, lambda x: 100 - 10 * x], {}, False, True),
    ("tangled (8 curves)", [(lambda x, k=k: 50 + 45 * math.sin(0.6 * x + k)) for k in range(8)],
     {}, True, True),
    ("no axes", [_exp], {"frame": False, "ticks": False}, False, False),
]


def cal_err(cal) -> float:
    """Worst endpoint error of the recovered axis ranges, % of the true span."""
    xe = max(abs(cal.x_range[0] - 0.0), abs(cal.x_range[1] - 10.0)) / 10.0
    ye = max(abs(cal.y_range[0] - 0.0), abs(cal.y_range[1] - 100.0)) / 100.0
    return 100.0 * max(xe, ye)


def point_err(series, curves) -> float | None:
    """Mean |y - nearest truth curve| over the model's points, % of the y range."""
    errs = [min(abs(y - c(x)) for c in curves) for s in series for x, y in s if 0 <= x <= 10]
    return sum(errs) / len(errs) if errs else None


def main():
    args = [a for a in sys.argv[1:]]
    consensus = 1
    if "--consensus" in args:
        i = args.index("--consensus")
        consensus = int(args[i + 1])
        del args[i:i + 2]
    model = args[0] if args else None
    describer = None
    if model:
        from pdf2md.describe import OpenAIVisionDescriber
        describer = OpenAIVisionDescriber("http://localhost:11434/v1", model,
                                          timeout=600.0, max_retries=1)
    reader = load_figure_ocr()
    print(f"{'CASE':22}{'AMB':>5}{'GATE':>6}{'CAL-ERR%':>10}{'SKEW':>6}", end="")
    print(f"{'PTS':>5}{'Y-ERR%':>8}{'CONF':>6}  VERDICT" if model else "  VERDICT")
    print("-" * (78 if model else 60))
    with tempfile.TemporaryDirectory() as td:
        for name, curves, kw, should_gate, expects_cal in CASES:
            path = Path(td) / f"{name}.png"
            draw_chart(path, curves, **kw)
            scan = analyze_raster(path, reader)
            gated = scan.ambiguity > AMBIGUITY_MAX
            err = cal_err(scan.calibration) if scan.calibration else None
            bad = (gated != should_gate
                   or (expects_cal and not gated and (err is None or err > 3.0))
                   or (not expects_cal and scan.calibration is not None))
            verdict = ("WRONG GATE" if gated != should_gate
                       else "ok" if not bad else "BAD CALIBRATION")
            row = (f"{name:22}{scan.ambiguity:>5.1f}{'yes' if gated else 'no':>6}"
                   f"{err if err is None else round(err, 2)!s:>10}{scan.skew:>6.1f}")
            if model and not gated and scan.calibration:
                if consensus > 1:
                    from pdf2md.digitize_vlm import vlm_digitize_consensus
                    d = vlm_digitize_consensus(path, describer, scan.calibration,
                                               votes=consensus)
                else:
                    d = vlm_digitize(path, describer, scan.calibration)
                if d is None or not d.series:
                    row += f"{'-':>5}{'-':>8}{'-':>6}  {verdict}; model returned nothing"
                else:
                    fit = pixel_fit(path, d.series)
                    conf = round(d.confidence * fit, 2)
                    pe = point_err(d.series, curves)
                    npts = sum(len(s) for s in d.series)
                    row += (f"{npts:>5}{pe if pe is None else round(pe, 1)!s:>8}{conf:>6.2f}"
                            f"  {verdict}; {'PRINTS' if conf >= 0.5 else 'withheld'}")
            else:
                row += (f"{'-':>5}{'-':>8}{'-':>6}  {verdict}" if model else f"  {verdict}")
            print(row)


if __name__ == "__main__":
    main()
