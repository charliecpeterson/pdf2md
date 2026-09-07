"""Score the table audit's findings against a second engine's reading.

    uv run python scripts/eval_table_audit_cross_engine.py A_DIR B_DIR [--json OUT]

The table audit raises more findings than any other check and has the least
external validation: a labelled set of modest size, and on scans no independent
reader at all. The glyph checks have poppler; a scanned table has no glyph layer
to appeal to.

A second engine converting the same page is that reader. Where two parsers
produce a table over the same region, the values they agree on are read twice,
and the ones only one of them has are where a person should look. This does not
say which engine is right -- it says where they disagree, and whether the audit
predicted it.

The claim under test was that a finding means something: tables the audit flags
should lose more of the other engine's values than tables it passes. That was
stated before the run, and **the run refuted it.** Scoring MinerU's audit against
Docling over ten scans, where the flag rate is 51% and the control is therefore
real (101 flagged, 98 passed), flagged tables agree *more*: median 0.40 against
0.29. Stratifying by table size does not rescue it -- the direction flips band to
band on samples of five to thirteen.

The reason is that this proxy is close to orthogonal to what the audit claims.
Two engines OCR'ing the same scanned table recover much the same *values* off the
same ink, and differ in how they arrange them; arrangement is the entire subject
of `merged_cells`, `shifted_values` and `header_absorbed_data`. A value-multiset
comparison cannot see the defect, so its silence is not evidence either way. A
version of this that measured arrangement -- which row and column each engine
puts a value in -- would be the real test, and needs an alignment between grids
of different shapes that this does not attempt.

Two things it did establish, which is why it is kept. Scoring Docling's audit is
not possible on scans at all: 127 of its 138 tables are flagged, so the control
is ten tables and nothing can be concluded from it. And `decimal_separator_lost`
sits far below every other kind in that direction (median 0.26 against 0.63-0.74
for the rest, n=5) -- the one finding class whose tables really do disagree with
a second reading.

Tables are matched by page and box overlap, not by index: the engines find
different numbers of tables (217 against 138 over ten scans), so position is
meaningless and a region either overlaps or it does not.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

_NUMBER = re.compile(r"[-+−]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?")
# Below this share of the smaller box, two tables are neighbours rather than the
# same printed region.
_MIN_OVERLAP = 0.5


def _box(raw):
    if not raw:
        return None
    x0, x1 = sorted((raw["x0"], raw["x1"]))
    y0, y1 = sorted((raw["y0"], raw["y1"]))
    return x0, y0, x1, y1


def _overlap(a, b) -> float:
    if a is None or b is None:
        return 0.0
    wide = min(a[2], b[2]) - max(a[0], b[0])
    tall = min(a[3], b[3]) - max(a[1], b[1])
    if wide <= 0 or tall <= 0:
        return 0.0
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return (wide * tall) / smaller if smaller > 0 else 0.0


def _values(text: str) -> collections.Counter:
    return collections.Counter(
        v.replace(",", "").replace("−", "-") for v in _NUMBER.findall(text or "")
    )


def _load(version_dir: Path) -> list[dict]:
    provenance = json.loads((version_dir / "provenance.json").read_text())
    findings = {}
    for tj in (version_dir / "data" / "tables").glob("*.json"):
        record = json.loads(tj.read_text())
        if "grid_audit" not in record:
            continue
        findings[record["block_id"]] = [
            f["kind"] for f in (record["grid_audit"] or {}).get("findings") or []
        ]
    return [
        {
            "page": t["page"],
            "box": _box(t.get("bbox")),
            "values": _values(t.get("gfm") or t.get("html") or ""),
            "findings": findings.get(t["block_id"], []),
        }
        for t in provenance["tables"]
    ]


def compare(a_dir: Path, b_dir: Path) -> list[dict]:
    by_hash = {}
    for root, side in ((a_dir, "a"), (b_dir, "b")):
        for prov in sorted(root.glob("*/v*/provenance.json")):
            key = json.loads(prov.read_text())["source_sha256"]
            by_hash.setdefault(key, {})[side] = prov.parent

    rows = []
    for key, sides in by_hash.items():
        if len(sides) != 2:
            continue
        a_tables, b_tables = _load(sides["a"]), _load(sides["b"])
        for a in a_tables:
            best, share = None, 0.0
            for b in b_tables:
                if b["page"] != a["page"]:
                    continue
                current = _overlap(a["box"], b["box"])
                if current > share:
                    best, share = b, current
            if best is None or share < _MIN_OVERLAP:
                rows.append({"document": sides["a"].parent.name, "page": a["page"],
                             "flagged": bool(a["findings"]), "kinds": a["findings"],
                             "matched": False})
                continue
            shared = sum((a["values"] & best["values"]).values())
            other = sum(best["values"].values())
            rows.append({
                "document": sides["a"].parent.name, "page": a["page"],
                "flagged": bool(a["findings"]), "kinds": a["findings"],
                "matched": True, "overlap": round(share, 2),
                "other_values": other, "shared_values": shared,
                "agreement": round(shared / other, 4) if other else None,
            })
    return rows


def report(rows: list[dict]) -> None:
    matched = [r for r in rows if r["matched"] and r.get("agreement") is not None]
    print(f"{len(rows)} tables in the first engine, {len(matched)} matched to a region "
          f"the second engine also read")
    unmatched = [r for r in rows if not r["matched"]]
    print(f"  unmatched: {len(unmatched)} "
          f"({sum(1 for r in unmatched if r['flagged'])} of them flagged)")

    def band(subset):
        if not subset:
            return "n/a"
        subset = sorted(r["agreement"] for r in subset)
        mid = subset[len(subset) // 2]
        return (f"n={len(subset):4} median {mid:.2f}  "
                f"mean {sum(subset) / len(subset):.2f}")

    flagged = [r for r in matched if r["flagged"]]
    clean = [r for r in matched if not r["flagged"]]
    print(f"\n  audit flagged   {band(flagged)}")
    print(f"  audit passed    {band(clean)}")
    print("\n  by finding kind (median agreement with the other engine's values):")
    per = collections.defaultdict(list)
    for r in flagged:
        for kind in set(r["kinds"]):
            per[kind].append(r["agreement"])
    for kind, values in sorted(per.items(), key=lambda kv: -len(kv[1])):
        values.sort()
        print(f"    {kind:32} {band([{'agreement': v} for v in values])}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("a_dir", type=Path, help="the engine whose audit is scored")
    parser.add_argument("b_dir", type=Path, help="the second reading")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()
    rows = compare(args.a_dir, args.b_dir)
    report(rows)
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    main()
