"""Measure vector-chart digitization accuracy against synthetic ground truth.

    uv run --with matplotlib python scripts/eval_digitize.py

Unlike the labelled evals (equations, ocr) there's no hand truth here: we *generate*
born-digital plots whose data we already know, digitize them, and score the recovery.
Each case probes a different condition (multi-series, negatives, decimals, dense, log,
scatter, crossing) so the table shows where the digitizer stays near-lossless and where
it breaks. The load-bearing check is the last column: confidence must *track* error --
a wrong extraction that reports high confidence is the dangerous failure, a wrong one
that reports low confidence is the system working as designed (flag-don't-fabricate).

matplotlib is not a pdf2md dependency, so run this dev harness with `uv run --with
matplotlib`. Deterministic: fixed formulas, no randomness.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

import matplotlib
matplotlib.use("pdf")
import matplotlib.pyplot as plt  # noqa: E402
import pypdfium2 as pdfium  # noqa: E402

from pdf2md.digitize import VectorPathDigitizer  # noqa: E402
from pdf2md.schema import BBox  # noqa: E402

X = list(range(0, 11))


def _plot(path, series, xlim, ylim, **kw):
    fig, ax = plt.subplots(figsize=(4, 3))
    for y in series.values():
        ax.plot(X[:len(y)], y, marker="o", **kw)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    fig.savefig(path)
    plt.close(fig)


def case_baseline(path):
    s = {"lin": X}
    _plot(path, s, (0, 10), (0, 10))
    return s, (0, 10), (0, 10)


def case_multi(path):
    s = {"lin": X, "sq": [v * v / 10 for v in X], "half": [v / 2 for v in X]}
    _plot(path, s, (0, 10), (0, 10))
    return s, (0, 10), (0, 10)


def case_negative(path):
    s = {"cos": [round(5 * math.cos(v / 2), 3) for v in X]}
    _plot(path, s, (0, 10), (-5, 5))
    return s, (0, 10), (-5, 5)


def case_decimals(path):  # decimal y-ticks (0.0..1.0); x kept in range
    s = {"frac": [round(v / 10, 3) for v in X]}
    _plot(path, s, (0, 10), (0, 1))
    return s, (0, 10), (0, 1)


def case_crossing(path):
    s = {"up": X, "down": [10 - v for v in X]}
    _plot(path, s, (0, 10), (0, 10))
    return s, (0, 10), (0, 10)


def case_scatter(path):  # markers, no connecting line -> a known limitation to expose
    s = {"pts": [v * v / 10 for v in X]}
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot(X, s["pts"], marker="o", linestyle="none")
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    fig.savefig(path)
    plt.close(fig)
    return s, (0, 10), (0, 10)


def case_scatter2(path):  # two marker styles -> separated into series by marker appearance
    s = {"up": X, "down": [10 - v for v in X]}
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot(X, s["up"], marker="o", linestyle="none")
    ax.plot(X, s["down"], marker="s", linestyle="none")
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    fig.savefig(path)
    plt.close(fig)
    return s, (0, 10), (0, 10)


def case_bars(path):  # filled rects on a common baseline -> (x center, top) per bar
    s = {"bars": [3, 7, 5, 9, 4, 6, 2, 8, 5, 7, 4]}
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.bar(X, s["bars"])
    ax.set_ylim(0, 10)
    fig.savefig(path)
    plt.close(fig)
    return s, (0, 10), (0, 10)


def case_subplots(path):  # two panels, different y scales -> per-panel calibration
    s = {"up": X, "down": [100 - 10 * v for v in X]}
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(4, 5))
    a1.plot(X, s["up"], marker="o")
    a1.set_xlim(0, 10)
    a1.set_ylim(0, 10)
    a2.plot(X, s["down"], marker="o")
    a2.set_xlim(0, 10)
    a2.set_ylim(0, 100)
    fig.savefig(path)
    plt.close(fig)
    return s, (0, 10), (0, 100)


def case_logy(path):  # log axis -> the linear calibration should fit poorly (low R^2)
    s = {"exp": [10 ** (v / 3) for v in X]}
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot(X, s["exp"], marker="o")
    ax.set_yscale("log")
    ax.set_xlim(0, 10)
    fig.savefig(path)
    plt.close(fig)
    return s, (0, 10), None


CASES = [
    ("baseline linear", case_baseline),
    ("multi-series (3)", case_multi),
    ("negative y", case_negative),
    ("decimal ticks 0-1", case_decimals),
    ("crossing curves", case_crossing),
    ("scatter (no line)", case_scatter),
    ("scatter, 2 series", case_scatter2),
    ("bar chart", case_bars),
    ("two panels", case_subplots),
    ("log-y axis", case_logy),
]


def _match_error(recovered, truth, xlim, ylim):
    """Greedily match recovered series to truth series and return (x_err%, y_err%) over
    matched points, normalised by axis range. None if series counts don't line up."""
    truths = [sorted(zip(X[:len(ys)], ys)) for ys in truth.values()]
    recs = [sorted(s) for s in recovered]
    if len(recs) != len(truths):
        return None
    xr = (xlim[1] - xlim[0]) or 1
    yr = ((ylim[1] - ylim[0]) if ylim else (max(max(t[1] for t in ts) for ts in truths) or 1)) or 1
    used, xe, ye, n = set(), 0.0, 0.0, 0
    for t in truths:
        best, bi = 1e9, None
        for i, r in enumerate(recs):
            if i in used or len(r) != len(t):
                continue
            d = sum(abs(rp[1] - tp[1]) for rp, tp in zip(r, t)) / len(t)
            if d < best:
                best, bi = d, i
        if bi is None:
            return None
        used.add(bi)
        for rp, tp in zip(recs[bi], t):
            xe += abs(rp[0] - tp[0]); ye += abs(rp[1] - tp[1]); n += 1
    return 100 * xe / n / xr, 100 * ye / n / yr


def main():
    dig = VectorPathDigitizer()
    print(f"{'CASE':22}{'TRUTH':>6}{'GOT':>5}{'CONF':>6}{'X-ERR%':>8}{'Y-ERR%':>8}  VERDICT")
    print("-" * 78)
    with tempfile.TemporaryDirectory() as td:
        for name, gen in CASES:
            path = Path(td) / f"{name}.pdf"
            truth, xlim, ylim = gen(path)
            doc = pdfium.PdfDocument(str(path)); w, h = doc[0].get_size(); doc.close()
            res = dig.digitize(path, 1, BBox(x0=0, y0=0, x1=w, y1=h))
            if res is None:
                print(f"{name:22}{len(truth):>6}{'-':>5}{'-':>6}{'-':>8}{'-':>8}  no extraction (fell back to crop)")
                continue
            err = _match_error(res.series, truth, xlim, ylim)
            conf = res.confidence
            if err is None:
                print(f"{name:22}{len(truth):>6}{len(res.series):>5}{conf:>6.2f}{'?':>8}{'?':>8}  series mismatch")
                continue
            xe, ye = err
            bad = max(xe, ye) > 2.0
            verdict = ("CONFIDENT-WRONG (bad!)" if bad and conf > 0.7
                       else "flagged low-conf" if bad
                       else "near-lossless" if max(xe, ye) < 0.5 else "ok")
            print(f"{name:22}{len(truth):>6}{len(res.series):>5}{conf:>6.2f}{xe:>8.2f}{ye:>8.2f}  {verdict}")


if __name__ == "__main__":
    main()
