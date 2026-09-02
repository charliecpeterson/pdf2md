"""Compare two engines' table grids for the same document, and ask whether the
row/grid audit flags the tables they disagree about.

    uv run python scripts/eval_engine_table_agreement.py A_VERSION_DIR B_VERSION_DIR
                                                         [--json OUT] [--quiet]

Two parsers reading the same page are an independent opinion on it. Where they
agree cell for cell, the extraction is very likely right whatever either says
about itself; where they disagree, one of them is wrong and the reader needs to
know which tables those are.

The point of running it is the contingency at the end. `table_audit` was
calibrated against thirteen hand-labelled tables, which is a thin basis for
trusting it. Engine disagreement is unlabelled but plentiful, and a check worth
having should flag the tables the two engines fight over and stay quiet on the
ones they agree about. That is measured here, not assumed.

Both directories must be completed bundles of the *same source*; the sources are
hashed and a mismatch fails loudly.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

from pdf2md.tables import gfm_rows, html_tables

# How much two tables' boxes must overlap to be the same printed table.
_MIN_IOU = 0.3
# A value, as opposed to a label. Engines render a header differently -- one
# writes `angle $\\theta_0$ (deg)` where the other writes `angle θ0 (deg)`,
# one keeps the accent in `Rappé` -- and none of that is a disagreement about
# the data. Numbers are where the two either agree about the page or don't.
_NUMBER = re.compile(r"-?\d(?:[\d,]*\d)?(?:\.\d+)?$")


def _load(version_dir: Path) -> dict:
    provenance = version_dir / "provenance.json"
    if not provenance.is_file():
        raise SystemExit(f"not a completed bundle: {version_dir}")
    return json.loads(provenance.read_text())


def _engine(document: dict) -> str:
    versions = (document.get("provenance") or {}).get("engine_versions") or {}
    for name in ("mineru", "docling", "stored"):
        if name in versions:
            return name
    return "unknown"


def _cells(table: dict) -> list[str]:
    gfm = table.get("gfm") or ""
    rows = gfm_rows(gfm) if gfm.strip() else (html_tables(table.get("html") or "") or [[]])[0]
    return [cell for row in rows for cell in row]


def _glyph_cells(table: dict) -> list[str]:
    """The glyph-truth reconstruction we ship beside the engine's grid.

    It has never been scored against anything. If it is no closer to a second
    parser's reading than the engine's own grid is, it is a file we write for no
    reason."""
    text = table.get("glyph_grid") or ""
    return [cell for row in gfm_rows(text) for cell in row] if text.strip() else []


def _norm(value: str) -> str:
    """Comparable cell content: the engines differ in spacing and in how they
    render a minus, and neither difference is a disagreement about the page."""
    value = unicodedata.normalize("NFKC", value)
    value = value.translate(str.maketrans({"−": "-", "‑": "-", "–": "-"}))
    return re.sub(r"\s+", "", value).strip().lower()


def _value_rate(mine: Counter, theirs: Counter) -> float | None:
    """Agreement over values only, for whichever pair of readings is passed."""
    my_values = Counter({v: n for v, n in mine.items() if _NUMBER.match(v)})
    their_values = Counter({v: n for v, n in theirs.items() if _NUMBER.match(v)})
    total = sum((my_values | their_values).values())
    return sum((my_values & their_values).values()) / total if total else None


def _iou(a: dict | None, b: dict | None) -> float:
    if not a or not b:
        return 0.0
    ax0, ax1 = sorted((a["x0"], a["x1"]))
    ay0, ay1 = sorted((a["y0"], a["y1"]))
    bx0, bx1 = sorted((b["x0"], b["x1"]))
    by0, by1 = sorted((b["y0"], b["y1"]))
    ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0.0, min(ay1, by1) - max(ay0, by0))
    overlap = ix * iy
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - overlap
    return overlap / union if union > 0 else 0.0


def _pair_tables(left: dict, right: dict) -> list[tuple[dict, dict | None]]:
    """Match each left table to the right table covering the same page region."""
    remaining = list(right.get("tables", []))
    pairs: list[tuple[dict, dict | None]] = []
    for table in left.get("tables", []):
        candidates = [
            (_iou(table.get("bbox"), other.get("bbox")), other)
            for other in remaining
            if other.get("page") == table.get("page")
        ]
        best = max(candidates, key=lambda item: item[0], default=(0.0, None))
        if best[0] >= _MIN_IOU:
            remaining.remove(best[1])
            pairs.append((table, best[1]))
        else:
            pairs.append((table, None))
    return pairs


def compare(left_dir: Path, right_dir: Path) -> dict:
    left, right = _load(left_dir), _load(right_dir)
    if left["source_sha256"] != right["source_sha256"]:
        raise SystemExit(
            f"different sources: {left['source_sha256'][:12]} != "
            f"{right['source_sha256'][:12]}"
        )

    rows: list[dict] = []
    for table, other in _pair_tables(left, right):
        mine = Counter(_norm(c) for c in _cells(table) if _norm(c))
        glyph = Counter(
            _norm(c) for c in _glyph_cells(table) if _norm(c)
        )
        theirs = Counter(_norm(c) for c in _cells(other) if _norm(c)) if other else Counter()
        shared = sum((mine & theirs).values())
        total = sum((mine | theirs).values())
        my_values = Counter({v: n for v, n in mine.items() if _NUMBER.match(v)})
        their_values = Counter({v: n for v, n in theirs.items() if _NUMBER.match(v)})
        value_shared = sum((my_values & their_values).values())
        value_total = sum((my_values | their_values).values())
        # One engine reading nothing where the other read a full table is that
        # engine failing, not the two disagreeing about the page. Counting it as
        # disagreement made the audit look like it had missed a defect that was
        # never there, and understated its recall.
        empty_side = bool(mine) != bool(theirs)
        rows.append({
            "block_id": table["block_id"],
            "page": table["page"],
            "matched": other is not None,
            "one_side_empty": empty_side,
            "cells_left": sum(mine.values()),
            "cells_right": sum(theirs.values()),
            "agreement": shared / total if total else None,
            "value_agreement": value_shared / value_total if value_total else None,
            # None, not zero, when there is no reconstruction to judge: scoring
            # an absent grid as total disagreement would make the comparison a
            # measure of how often one exists.
            "glyph_value_agreement": _value_rate(glyph, theirs) if glyph else None,
            "only_left": [v for v, n in (mine - theirs).items() for _ in range(n)][:8],
            "only_right": [v for v, n in (theirs - mine).items() for _ in range(n)][:8],
            "audit_findings": sorted(
                f["kind"] for f in (table.get("grid_audit") or {}).get("findings", [])
            ),
        })
    return {
        "source_sha256": left["source_sha256"],
        "left": {"dir": str(left_dir), "engine": _engine(left)},
        "right": {"dir": str(right_dir), "engine": _engine(right)},
        "tables": rows,
    }


def report(result: dict, quiet: bool = False) -> None:
    left, right = result["left"]["engine"], result["right"]["engine"]
    print(f"{left} (left) vs {right} (right), source {result['source_sha256'][:12]}")
    if not quiet:
        print()
        print(f"{'TABLE':22s} {'PG':>3s} {'CELLS':>11s} {'ALL':>5s} {'VALUES':>6s}  AUDIT")
        print("-" * 84)
        for row in result["tables"]:
            def rate(key: str) -> str:
                return "-" if row[key] is None else f"{row[key]:.2f}"
            cells = f"{row['cells_left']:>4d}/{row['cells_right']:<4d}"
            audit = ", ".join(row["audit_findings"]) or "-"
            print(f"{row['block_id']:22s} {row['page']:>3d} {cells:>11s} "
                  f"{rate('agreement'):>5s} {rate('value_agreement'):>6s}  {audit}")

    # The measurement this script exists for: does the audit fire where the two
    # engines disagree, and stay quiet where they agree?
    matched = [
        row for row in result["tables"]
        if row["value_agreement"] is not None and not row.get("one_side_empty")
    ]
    grid = Counter()
    for row in matched:
        disputed = row["value_agreement"] < 1.0
        flagged = bool(row["audit_findings"])
        grid[(disputed, flagged)] += 1
    print()
    print("audit findings vs engine disagreement about *values* (matched tables)")
    print(f"{'':18s} {'audit flagged':>14s} {'audit silent':>13s}")
    print(f"{'engines differ':18s} {grid[(True, True)]:>14d} {grid[(True, False)]:>13d}")
    print(f"{'engines agree':18s} {grid[(False, True)]:>14d} {grid[(False, False)]:>13d}")
    walkover = sum(1 for row in result["tables"] if row.get("one_side_empty"))
    if walkover:
        print(f"\n{walkover} table(s) excluded: one engine read nothing there, which is "
              f"that engine failing rather than the two disagreeing.")
    scored = [
        row for row in matched
        if row["glyph_value_agreement"] is not None and row["value_agreement"] is not None
    ]
    if scored:
        better = sum(
            1 for row in scored
            if row["glyph_value_agreement"] > row["value_agreement"]
        )
        worse = sum(
            1 for row in scored
            if row["glyph_value_agreement"] < row["value_agreement"]
        )
        print()
        print(f"glyph-truth grid vs the engine's, judged against {right}: "
              f"{better} closer, {worse} further, {len(scored) - better - worse} level "
              f"(of {len(scored)})")
    unmatched = len(result["tables"]) - len(matched) - walkover
    if unmatched:
        print(f"\n{unmatched} table(s) had no counterpart in the other bundle.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("left", type=Path, help="version directory of one engine's bundle")
    parser.add_argument("right", type=Path, help="version directory of the other's")
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--quiet", action="store_true", help="contingency only")
    args = parser.parse_args()

    result = compare(args.left, args.right)
    report(result, quiet=args.quiet)
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
