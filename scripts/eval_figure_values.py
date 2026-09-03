"""Score extracted chart values against points read off the printed chart.

    uv run python scripts/eval_figure_values.py OUT_DIR [--labels FILE] [--check]

Three things guard chart extraction and none of them looks at the numbers.
`figure_axes_labels.json` asks whether the *figure* was recoverable.
`_tick_range_fraction` asks whether the mapping is absurd by orders of magnitude.
R^2 asks whether the tick labels sit on a line. A curve read at 0.42 where the
page prints 0.38 passes all three, and that is the failure that matters most for
a converter an agent reads without the PDF beside it.

`tests/figure_values_labels.json` holds anchor points read off the rendered crop
-- a curve's start, its peak, its endpoint -- with the printed axis ranges. Never
from the digitizer: the crops were read before any emitted series was looked at.

An anchor is MATCHED when some emitted point sits within `_X_TOL` of its x (as a
fraction of the x span, or a factor on a log axis) and within `_Y_TOL` of its y
(likewise). A figure's accuracy is the share of its anchors matched. A figure
matching none of its anchors is emitting numbers that are not on the printed
chart, whatever its confidence says.

Anchors are eyeball readings, so `_Y_TOL` is deliberately loose: it is measuring
whether the extraction landed on the curve, not the last digit.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_X_TOL = 0.04   # fraction of the x span
_Y_TOL = 0.05   # fraction of the y span
_LOG_TOL = 0.15  # decades, on a log axis


def _near(value: float, target: float, axis: dict, tol: float) -> bool:
    if axis["kind"] == "log":
        if value <= 0 or target <= 0:
            return False
        return abs(math.log10(value / target)) <= _LOG_TOL
    span = abs(axis["max"] - axis["min"]) or 1.0
    return abs(value - target) <= tol * span


def score(row: dict, series: list) -> tuple[int, int, list]:
    """An anchor matches when some SERIES passes through it.

    Not when some point does. "Any point near the anchor" rewards sprawl: Atkins
    #/pictures/96 emits 426 points spanning x -1243..1693 against a printed axis of
    0..450, which is verifiably wrong, and it scored 2 of 2 because a series that
    covers everything covers the anchors too. Asking each series for its own nearest
    point in x, and comparing only that one, is the question actually meant -- does
    this curve pass through the place the page says it does."""
    matched, misses = 0, []
    for anchor in row["anchors"]:
        hit = False
        for one in series:
            near_x = [(x, y) for x, y in one
                      if _near(x, anchor["x"], row["x_axis"], _X_TOL)]
            if not near_x:
                continue
            x, y = min(near_x, key=lambda p: abs(p[0] - anchor["x"]))
            if _near(y, anchor["y"], row["y_axis"], _Y_TOL):
                hit = True
                break
        matched += hit
        if not hit:
            misses.append(anchor)
    return matched, len(row["anchors"]), misses


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--labels", type=Path,
                        default=_ROOT / "tests" / "figure_values_labels.json")
    parser.add_argument("--check", action="store_true",
                        help="Fail if a shipped extraction matches none of its anchors.")
    args = parser.parse_args()

    labels = json.loads(args.labels.read_text())
    figures: dict[tuple[str, str], dict] = {}
    for document in sorted(args.out_dir.iterdir()):
        versions = sorted(document.glob("v*/provenance.json"),
                          key=lambda p: int(p.parent.name[1:])) if document.is_dir() else []
        if not versions:
            continue
        for figure in json.loads(versions[-1].read_text()).get("figures", []):
            figures[(document.name, figure["block_id"])] = figure

    rows, blind = [], []
    print(f"{'document':<26}{'block':<15}{'method':<22}{'conf':>6}{'matched':>10}")
    for row in labels:
        figure = figures.get((row["document"], row["block_id"]))
        if figure is None:
            print(f"  {row['document'][:24]:<26}{row['block_id']:<15}(not in this output)")
            continue
        status = figure.get("data_extraction_status")
        if status != "extracted":
            print(f"  {row['document'][:24]:<24}{row['block_id']:<15}"
                  f"{'-- ' + str(status):<22}{'':>6}{'not shipped':>12}")
            continue
        digitization = figure["digitization"]
        matched, total, misses = score(row, digitization["series"])
        rows.append((row, matched, total, misses))
        if matched == 0:
            blind.append(row)
        print(f"  {row['document'][:24]:<24}{row['block_id']:<15}"
              f"{digitization['method']:<22}{digitization['confidence']:>6}"
              f"{f'{matched}/{total}':>12}")

    if rows:
        anchors = sum(t for _, _, t, _ in rows)
        hits = sum(m for _, m, _, _ in rows)
        print(f"\nshipped extractions scored: {len(rows)}")
        print(f"anchors matched: {hits}/{anchors} ({hits / anchors:.0%})")
        print(f"extractions matching NO anchor: {len(blind)}/{len(rows)}")
        for row in blind:
            print(f"   {row['document'][:26]:<28}{row['block_id']:<15}{row['note'][:52]}")
    raise SystemExit(1 if (args.check and blind) else 0)


if __name__ == "__main__":
    main()
