"""Does value damage track the text layer's fitness?

PROBE, recorded in docs/accuracy-improvement-notes.md. Answer: yes -- a document
the unfit-layer verdict flags carries 2.4x the value damage per table. Also shows
why "did Docling OCR it" is the wrong way to classify a scan: an old paper
carrying someone else's OCR as a text layer never reaches the scanned path.
"""
import json
import sys
from collections import Counter
from pathlib import Path

VALUE_KINDS = {"stray_glyphs_in_numeric_column", "decimal_separator_lost"}
STRUCT_KINDS = {"merged_cells", "shifted_values", "row_count", "dropped_row_content",
                "merged_rows", "header_absorbed_data", "dropped_column", "partial_grid"}

rows = []
for doc in sorted(d for d in Path(sys.argv[1]).iterdir() if d.is_dir()):
    vs = [v for v in doc.glob("v*") if (v / "provenance.json").is_file()]
    if not vs:
        continue
    vdir = max(vs, key=lambda v: int(v.name[1:]))
    d = json.loads((vdir / "provenance.json").read_text())
    prof = {}
    pf = vdir / "profile.json"
    if pf.is_file():
        prof = json.loads(pf.read_text())
    value = struct = 0
    for t in d.get("tables") or []:
        for f in (t.get("grid_audit") or {}).get("findings") or []:
            if f["kind"] in VALUE_KINDS:
                value += 1
            elif f["kind"] in STRUCT_KINDS:
                struct += 1
    unfit = any(f.get("block_id") == "#/document"
                for f in (d.get("coverage") or {}).get("flags") or [])
    ocr = prof.get("ocr_pages", prof.get("scanned_pages", 0)) or 0
    rows.append({
        "doc": doc.name, "pages": d.get("page_count", 0), "tables": len(d.get("tables") or []),
        "ocr": ocr, "value": value, "struct": struct, "unfit": unfit,
    })

# Classifying by "did Docling OCR it" puts the 1971-1984 JMB papers in the
# born-digital pile: they are scans carrying someone else's OCR as a text layer,
# so nothing routes them down the scanned path. The tool's own verdict is the
# better split, because that is exactly what it was added to say.
unfit = [r for r in rows if r["unfit"]]
fit = [r for r in rows if not r["unfit"]]
for label, group in (("text layer judged unfit", unfit), ("text layer fit", fit)):
    if not group:
        continue
    t = sum(r["tables"] for r in group)
    print(f"{label}: {len(group)} documents, {t} tables")
    print(f"    value findings   : {sum(r['value'] for r in group):4d}"
          f"  ({sum(r['value'] for r in group)/max(1,t):.2f} per table)")
    print(f"    structural       : {sum(r['struct'] for r in group):4d}"
          f"  ({sum(r['struct'] for r in group)/max(1,t):.2f} per table)")
    print(f"    unfit-layer docs : {sum(r['unfit'] for r in group)}/{len(group)}")
print("\nworst by value findings:")
for r in sorted(rows, key=lambda r: -r["value"])[:8]:
    tag = "scan" if r["ocr"] else "digital"
    print(f"  {r['doc'][:40]:40s} {tag:7s} p{r['pages']:3d} tables{r['tables']:3d} "
          f"value{r['value']:3d} struct{r['struct']:3d} {'UNFIT' if r['unfit'] else ''}")
