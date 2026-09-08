"""Re-apply the current detectors to already-converted tables.

The findings in provenance.json were written by whichever version of the check ran
at conversion time, and the stray-glyph detector has gained three guards since.
Re-running grid_findings over the stored cells gives today's numbers without
reconverting.
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "src")
from pdf2md.table_audit import grid_findings
from pdf2md.tables import gfm_rows

VALUE = {"stray_glyphs_in_numeric_column", "decimal_separator_lost"}
kinds, docs, tables = Counter(), Counter(), 0
for doc in sorted(d for d in Path(sys.argv[1]).iterdir() if d.is_dir()):
    vs = [v for v in doc.glob("v*") if (v / "provenance.json").is_file()]
    if not vs:
        continue
    d = json.loads((max(vs, key=lambda v: int(v.name[1:])) / "provenance.json").read_text())
    seen = set()
    for t in d.get("tables") or []:
        rows = gfm_rows(t.get("gfm") or "")
        if len(rows) < 2:
            continue
        tables += 1
        found = {f.kind for f in grid_findings(rows[0], rows[1:]) if f.kind in VALUE}
        for k in found:
            kinds[k] += 1
            seen.add(k)
    for k in seen:
        docs[k] += 1

print(f"  tables with a grid: {tables}")
for k in sorted(VALUE):
    print(f"  {k:34s}: {kinds[k]:3d} tables across {docs[k]} documents")
