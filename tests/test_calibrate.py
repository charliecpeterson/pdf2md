# The raster pre-scan (calibrate.py): synthetic PIL charts + a fake RapidOCR reader,
# no models. Covers tick calibration, the ambiguity gate, and its non-inflation by
# gridlines and in-plot text.
import math

import numpy as np
from PIL import Image, ImageDraw

from pdf2md.calibrate import AMBIGUITY_MAX, analyze_raster

L, R, T, B = 60, 380, 20, 340  # plot frame in a 440x400 canvas


class Res:
    def __init__(self, toks):  # toks: (text, cx, cy)
        self.txts = [t for t, _, _ in toks]
        self.scores = [0.9] * len(toks)
        self.boxes = np.array(
            [[[cx - 8, cy - 5], [cx + 8, cy - 5], [cx + 8, cy + 5], [cx - 8, cy + 5]]
             for _, cx, cy in toks], dtype=float)


TICKS = [("0", L, B + 20), ("0.5", (L + R) / 2, B + 20), ("1", R, B + 20),
         ("0", L - 30, B), ("5", L - 30, (T + B) / 2), ("10", L - 30, T)]


def reader_with(toks):
    def reader(arr):
        return Res(toks)
    return reader


def chart(tmp_path, curves, gridlines=0, text_at=None):
    img = Image.new("RGB", (440, 400), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([L, T, R, B], outline="black", width=2)
    for g in range(gridlines):
        y = T + (g + 1) * (B - T) / (gridlines + 1)
        d.line([L, y, R, y], fill="black")
    for c in curves:  # c maps u in 0..1 -> value in 0..1 (0 at the bottom of the frame)
        pts = [(L + u * (R - L), B - c(u) * (B - T)) for u in (i / 60 for i in range(61))]
        d.line(pts, fill="black", width=2)
    if text_at:
        d.rectangle([text_at[0], text_at[1], text_at[0] + 80, text_at[1] + 30], fill="black")
    p = tmp_path / "chart.png"
    img.save(p)
    return p


def test_calibrates_single_curve_chart(tmp_path):
    p = chart(tmp_path, [lambda u: 0.1 + 0.8 * u])
    scan = analyze_raster(p, reader_with(TICKS))
    assert scan is not None and scan.ambiguity <= 2
    cal = scan.calibration
    assert cal is not None and cal.x_kind == "linear" and cal.y_kind == "linear"
    assert abs(cal.x_range[0] - 0.0) < 0.06 and abs(cal.x_range[1] - 1.0) < 0.06
    assert abs(cal.y_range[0] - 0.0) < 0.6 and abs(cal.y_range[1] - 10.0) < 0.6
    assert cal.confidence > 0.5


def test_gates_tangled_overlapping_curves(tmp_path):
    curves = [lambda u, k=k: 0.5 + 0.45 * math.sin(6 * u + k) for k in range(8)]
    scan = analyze_raster(chart(tmp_path, curves), reader_with(TICKS))
    assert scan.ambiguity > AMBIGUITY_MAX


def test_gridlines_and_inplot_text_dont_inflate_ambiguity(tmp_path):
    # a black text box inside the plot is masked out via its OCR box, gridlines by shape
    toks = TICKS + [("PROFILES", 240, 115)]
    p = chart(tmp_path, [lambda u: 0.1 + 0.8 * u], gridlines=5, text_at=(200, 100))
    scan = analyze_raster(p, reader_with(toks))
    assert scan.ambiguity <= AMBIGUITY_MAX


def test_degrades_without_reader(tmp_path):
    # a diagonal, not flat, curve: a full-width horizontal line reads as a gridline
    scan = analyze_raster(chart(tmp_path, [lambda u: 0.2 + 0.6 * u]), None)
    assert scan is not None and scan.calibration is None
    assert scan.ambiguity >= 1  # the curve still counts as one trace


def test_tick_value_parses_ocr_tick_forms():
    from pdf2md.calibrate import tick_value

    assert tick_value("0.8") == 0.8
    assert tick_value("-4") == -4.0
    assert tick_value("10-2") == 0.01     # flattened log superscript: 10^-2
    assert tick_value("1e-05") == 1e-05   # scientific notation
    assert tick_value("103") == 103.0     # literal, NOT 10^3 (positive exponent is ambiguous)
    assert tick_value("10-2x") is None
    assert tick_value("R/Re") is None


def test_assume_upright_skips_orientation_hunt(tmp_path):
    # a reader that reports garbage uprightness would rotate the crop; assume_upright
    # must bypass it entirely (born-digital renders are always upright)
    p = chart(tmp_path, [lambda u: 0.1 + 0.8 * u])
    scan = analyze_raster(p, reader_with(TICKS), assume_upright=True)
    assert scan.angle == 0 and scan.calibration is not None


def test_unreadable_crop_returns_none(tmp_path):
    p = tmp_path / "not_an_image.png"
    p.write_text("nope")
    assert analyze_raster(p, None) is None
