"""Benchmark pdf2md conversion: time and coverage per document.

    uv run python scripts/benchmark.py PATH [PATH ...] [--no-formula] [--out DIR] [--json FILE]

Conversion time varies a lot with equation enrichment, so run with and without
`--no-formula` to compare. The engine is built once and reused; the first
document's time includes one-time model load. By default each document is
re-converted (`--force`) so timings are real rather than cached.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path

from pdf2md.config import Config
from pdf2md.pipeline import convert_file


def _pdfs(paths: list[str]) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        p = Path(raw)
        found.extend(sorted(p.rglob("*.pdf")) if p.is_dir() else [p])
    return found


def main() -> None:
    ap = argparse.ArgumentParser(description="Benchmark pdf2md conversion.")
    ap.add_argument("paths", nargs="+", help="PDF files or directories.")
    ap.add_argument("--no-formula", action="store_true", help="Disable formula→LaTeX enrichment.")
    ap.add_argument("--out", help="Output root (sets PDF2MD_OUT).")
    ap.add_argument("--json", dest="json_path", help="Write results as JSON for tracking.")
    ap.add_argument("--allow-cached", action="store_true", help="Don't force re-conversion.")
    args = ap.parse_args()

    if args.out:
        os.environ["PDF2MD_OUT"] = args.out
    cfg = Config(do_formula_enrichment=not args.no_formula)

    from pdf2md.engines.docling import DoclingEngine

    engine = DoclingEngine(
        formula_enrichment=cfg.do_formula_enrichment, artifacts_path=cfg.local_model_dir
    )

    rows: list[dict] = []
    hdr = f"{'DOC':40s} {'PAGES':>5} {'SEC':>8} {'PG/S':>6} {'BLK':>5} {'CROP':>4} {'FLAG':>4}  LOSSLESS"
    print(hdr)
    print("-" * len(hdr))
    for pdf in _pdfs(args.paths):
        t0 = time.perf_counter()
        r = convert_file(pdf, engine=engine, config=cfg, force=not args.allow_cached)
        dt = time.perf_counter() - t0
        if r.failed:
            print(f"{pdf.name[:40]:40s}  FAILED: {r.error}")
            rows.append({"doc": pdf.name, "failed": True, "error": r.error})
            continue
        c = r.coverage
        flagged = (c.flagged + c.dropped) if c else 0
        pps = r.page_count / dt if dt else 0.0
        print(
            f"{pdf.name[:40]:40s} {r.page_count:5d} {dt:8.1f} {pps:6.2f} "
            f"{c.total_blocks if c else 0:5d} {c.cropped if c else 0:4d} {flagged:4d}  "
            f"{bool(c and c.lossless)}"
        )
        rows.append(
            {
                "doc": pdf.name,
                "pages": r.page_count,
                "seconds": round(dt, 2),
                "pages_per_sec": round(pps, 3),
                "blocks": c.total_blocks if c else 0,
                "cropped": c.cropped if c else 0,
                "flagged": flagged,
                "lossless": bool(c and c.lossless),
            }
        )

    ok = [r for r in rows if not r.get("failed")]
    total_pages = sum(r["pages"] for r in ok)
    total_sec = sum(r["seconds"] for r in ok)
    overall = total_pages / total_sec if total_sec else 0.0
    print(
        f"\n{len(ok)}/{len(rows)} docs, {total_pages} pages, {total_sec:.1f}s, "
        f"{overall:.2f} pg/s overall (formula_enrichment={cfg.do_formula_enrichment})"
    )

    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(
                {
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "formula_enrichment": cfg.do_formula_enrichment,
                    "total_pages": total_pages,
                    "total_seconds": round(total_sec, 1),
                    "pages_per_sec": round(overall, 3),
                    "docs": rows,
                },
                indent=2,
            )
        )
        print(f"wrote {args.json_path}")


if __name__ == "__main__":
    main()
