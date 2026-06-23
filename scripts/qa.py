"""Quality audit / regression harness over pdf2md outputs.

    uv run python scripts/qa.py OUT_DIR [--check] [--update] [--baseline FILE]

Reads the latest version of every document under OUT_DIR (no reconversion) and
computes per-document quality signals — the things we keep fixing: dropped
content, split-ligature residue, unbalanced equation LaTeX, image-backing,
scanned-page detection, losslessness. `--check` compares against a committed
baseline and exits non-zero if a *hard invariant* regressed (lossless lost,
dropped/ligature/unbalanced counts rose); the rest are reported as drift so a
shift in image-backing or OCR pages is visible without failing the run. `--update`
rewrites the baseline after an intended change.

This is the labels-free half of the accuracy story: it can't say the LaTeX is
*correct*, but it catches the day it silently gets worse. Run it after a reconvert.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from pdf2md.legibility import is_garbage
from pdf2md.schema import PROSE_TYPES

# Counts that must never rise (and lossless must stay True). Everything else is
# informational drift — printed, never gated. `illegible` / `illegible_table_rows`
# guard the font-decode class: prose blocks (from provenance) and rendered table
# rows whose text is symbol-font garbage (broken ToUnicode) must never silently pass
# as readable. Tables are gated separately because cells aren't provenance blocks.
_INVARIANTS = ("dropped", "ligature_residual", "unbalanced_eq", "illegible",
               "illegible_table_rows")

# String forms of the prose types (provenance.json stores the enum value as a string).
_PROSE = {t.value for t in PROSE_TYPES}

_LIG = re.compile(r"\w (?:ff|fi|fl|ffi|ffl) \w")
_EQ = re.compile(r"^\$\$\n(.*?)\n\$\$", re.MULTILINE | re.DOTALL)
_OPEN, _CLOSE = re.compile(r"(?<!\\)\{"), re.compile(r"(?<!\\)\}")
_LEFT = re.compile(r"\\left(?![a-zA-Z])")
_RIGHT = re.compile(r"\\right(?![a-zA-Z])")


def _latest_signals(doc_dir: Path) -> dict | None:
    """Signals from the newest *complete* version — skip an interrupted run that
    left a version dir without a provenance.json/document.md."""
    versions = sorted(doc_dir.glob("v*"),
                      key=lambda p: int(p.name[1:]) if p.name[1:].isdigit() else -1,
                      reverse=True)
    for v in versions:
        sig = _signals(v)
        if sig:
            return sig
    return None


def _unbalanced(md: str) -> int:
    n = 0
    for body in _EQ.findall(md):
        if len(_OPEN.findall(body)) != len(_CLOSE.findall(body)):
            n += 1
        elif len(_LEFT.findall(body)) != len(_RIGHT.findall(body)):
            n += 1
    return n


def _signals(version_dir: Path) -> dict | None:
    # Single-document papers emit `document.md`; split books emit one `.md` per
    # section. Concatenate whatever is there so book outputs are audited too (the
    # md-based signals scan every section, not just a missing `document.md`).
    prov = version_dir / "provenance.json"
    md_files = sorted(version_dir.glob("*.md"))
    if not prov.exists() or not md_files:
        return None
    d = json.loads(prov.read_text())
    md = "\n".join(p.read_text() for p in md_files)
    blocks = d.get("blocks", [])

    def status(s: str) -> int:
        return sum(1 for b in blocks if b.get("coverage_status") == s)

    eqs = [b for b in blocks if b.get("type") == "equation"]
    total = len(blocks)
    buckets = status("emitted") + status("cropped") + status("flagged") + status("dropped")
    return {
        "source": Path(d.get("source_path", version_dir.name)).name,
        "pages": d.get("page_count", 0),
        "blocks": total,
        "lossless": total == buckets,
        "dropped": status("dropped"),
        "eq_total": len(eqs),
        "eq_image_backed": sum(1 for b in eqs if (b.get("extra") or {}).get("crop_path")),
        "ocr_pages": len({b["page"] for b in blocks if (b.get("extra") or {}).get("ocr")}),
        "tables": len(d.get("tables", [])),
        "ligature_residual": len(_LIG.findall(md)),
        "unbalanced_eq": _unbalanced(md),
        "illegible": sum(
            1 for b in blocks
            if b.get("type") in _PROSE and b.get("text", "").strip() and is_garbage(b["text"])
        ),
        # Table cells aren't provenance blocks, so scan the rendered GFM rows — this
        # is what catches a broken-font table the prose `illegible` count can't see.
        "illegible_table_rows": sum(
            1 for ln in md.splitlines() if ln.startswith("|") and is_garbage(ln)
        ),
    }


def _collect(out_dir: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for doc_dir in sorted(p for p in out_dir.iterdir() if p.is_dir()):
        sig = _latest_signals(doc_dir)
        if sig:
            out[sig["source"]] = sig
    return out


def _print_table(sigs: dict[str, dict]) -> None:
    hdr = (f"{'DOC':28s} {'PG':>3} {'BLK':>4} {'LOSS':>4} {'DROP':>4} "
           f"{'EQ':>3} {'IMG':>3} {'OCR':>3} {'TBL':>3} {'LIG':>3} {'UNBAL':>5} "
           f"{'ILLEG':>5} {'ILTBL':>5}")
    print(hdr)
    print("-" * len(hdr))
    for s in sigs.values():
        print(f"{s['source'][:28]:28s} {s['pages']:3d} {s['blocks']:4d} "
              f"{('OK' if s['lossless'] else 'NO'):>4} {s['dropped']:4d} "
              f"{s['eq_total']:3d} {s['eq_image_backed']:3d} {s['ocr_pages']:3d} "
              f"{s['tables']:3d} {s['ligature_residual']:3d} {s['unbalanced_eq']:5d} "
              f"{s['illegible']:5d} {s['illegible_table_rows']:5d}")


def _check(sigs: dict[str, dict], baseline: dict[str, dict]) -> list[str]:
    regressions: list[str] = []
    for source, cur in sigs.items():
        base = baseline.get(source)
        if base is None:
            print(f"  NEW: {source} (no baseline)")
            continue
        if base.get("lossless") and not cur["lossless"]:
            regressions.append(f"{source}: lossless True -> False")
        for key in _INVARIANTS:
            if cur[key] > base.get(key, 0):
                regressions.append(f"{source}: {key} {base.get(key, 0)} -> {cur[key]}")
    for source in baseline.keys() - sigs.keys():
        print(f"  MISSING: {source} (in baseline, not in outputs)")
    return regressions


def main() -> None:
    ap = argparse.ArgumentParser(description="Quality audit / regression check over pdf2md outputs.")
    ap.add_argument("out_dir", help="Output root (the `out/` directory).")
    ap.add_argument("--baseline", default="tests/qa_baseline.json")
    ap.add_argument("--check", action="store_true", help="Fail if a hard invariant regressed.")
    ap.add_argument("--update", action="store_true", help="Rewrite the baseline from current outputs.")
    args = ap.parse_args()

    sigs = _collect(Path(args.out_dir))
    if not sigs:
        raise SystemExit(f"no pdf2md outputs found under {args.out_dir}")
    _print_table(sigs)

    baseline_path = Path(args.baseline)
    if args.update:
        baseline_path.write_text(json.dumps(sigs, indent=2, sort_keys=True) + "\n")
        print(f"\nwrote baseline: {baseline_path} ({len(sigs)} docs)")
        return
    if args.check:
        baseline = json.loads(baseline_path.read_text()) if baseline_path.exists() else {}
        regressions = _check(sigs, baseline)
        if regressions:
            print("\nREGRESSIONS:")
            for r in regressions:
                print(f"  - {r}")
            raise SystemExit(1)
        print("\nno regressions against baseline.")


if __name__ == "__main__":
    main()
