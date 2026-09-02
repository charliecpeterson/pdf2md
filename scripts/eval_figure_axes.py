"""Score figure data extraction against hand-labelled figures.

    uv run python scripts/eval_figure_axes.py OUT_DIR [--labels FILE] [--check]

`data_extraction_status` says what the pipeline did with a figure. Nothing said
whether it was right, because the 840 `vector_archetype_unmatched` figures have
no ground truth: a refusal is correct for a schematic with symbolic axes and
wrong for a bar chart.

`tests/figure_axes_labels.json` is that ground truth for 50 figures, labelled by
looking at the rendered crop and never by consulting the digitizer -- the same
standard the table-audit labels were held to. Each carries whether it is a chart
at all, what its axes print, and `data_recoverable`: whether the figure as
printed carries enough scale to recover numbers (two numeric axes for a line or
scatter, a numeric value axis plus categories for a bar chart).

Two numbers matter and they are not symmetric:

  precision  of the figures the pipeline extracted, how many were recoverable.
             A false extraction invents data, so this must stay at 1.00.
  recall     of the recoverable figures, how many the pipeline extracted. This
             is the size of the gap, and it is currently poor by design rather
             than by accident -- the vector reader needs two numeric axes.

The sample is stratified by status and capped at four figures per document, so
it deliberately does NOT match the corpus's composition (76% of the unmatched
population is one textbook). Rates here describe the labelled set, and must not
be multiplied back up to the corpus.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def load_status(out_dir: Path) -> dict[tuple[str, str], str]:
    status: dict[tuple[str, str], str] = {}
    for document in sorted(out_dir.iterdir()):
        versions = sorted(document.glob("v*/provenance.json"),
                          key=lambda p: int(p.parent.name[1:])) if document.is_dir() else []
        if not versions:
            continue
        for figure in json.loads(versions[-1].read_text()).get("figures", []):
            status[(document.name, figure["block_id"])] = figure.get("data_extraction_status")
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--labels", type=Path,
                        default=_ROOT / "tests" / "figure_axes_labels.json")
    parser.add_argument("--check", action="store_true",
                        help="Fail if a figure the labels call unrecoverable was extracted.")
    args = parser.parse_args()

    labels = json.loads(args.labels.read_text())
    status = load_status(args.out_dir)

    grid: Counter = Counter()
    invented: list[dict] = []
    missing: dict[str, list[dict]] = {}
    for row in labels:
        got = status.get((row["document"], row["block_id"]))
        if got is None:
            grid["not in this output"] += 1
            continue
        extracted = got == "extracted"
        grid[(row["data_recoverable"], extracted)] += 1
        if extracted and not row["data_recoverable"]:
            invented.append(row)
        if row["data_recoverable"] and not extracted:
            missing.setdefault(got, []).append(row)

    tp = grid[(True, True)]
    fp = grid[(False, True)]
    fn = grid[(True, False)]
    tn = grid[(False, False)]
    print(f"labelled figures scored: {tp + fp + fn + tn}"
          + (f"   (not in this output: {grid['not in this output']})"
             if grid["not in this output"] else ""))
    print(f"  recoverable and extracted      {tp:3d}")
    print(f"  recoverable and NOT extracted  {fn:3d}")
    print(f"  unrecoverable and extracted    {fp:3d}   <- invents data")
    print(f"  unrecoverable and refused      {tn:3d}")
    if tp + fp:
        print(f"\nprecision {tp / (tp + fp):.2f}   of what it extracts, how much was recoverable")
    if tp + fn:
        print(f"recall    {tp / (tp + fn):.2f}   of what is recoverable, how much it extracts")

    if missing:
        print("\nrecoverable figures it did not extract, by what it said instead:")
        for got, rows in sorted(missing.items(), key=lambda kv: -len(kv[1])):
            print(f"  {len(rows):3d}  {got}")
            for row in rows[:6]:
                print(f"         {row['document'][:24]:26s} {row['block_id']:<14} "
                      f"{row['kind']:<8} {row['axes']:<26} {row['note'][:44]}")
    if invented:
        print("\nEXTRACTED FROM AN UNRECOVERABLE FIGURE:")
        for row in invented:
            print(f"  {row['document'][:24]:26s} {row['block_id']:<14} {row['note']}")
    raise SystemExit(1 if (args.check and invented) else 0)


if __name__ == "__main__":
    main()
