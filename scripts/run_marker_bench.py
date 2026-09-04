"""Convert the olmOCR-bench PDFs with Marker's own CLI and name the candidates the
way the scorer looks for them.

This measures Marker standalone, not pdf2md-on-Marker: it bounds the headroom an
engine adapter could reach before anyone writes one. Same machine, same scorer,
same naming as the pdf2md run, so the per-subset numbers are comparable.
"""
import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bench_dir", type=Path)
    ap.add_argument("--marker", default="marker_single")
    ap.add_argument("--system", default="marker")
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    pdf_root = args.bench_dir / "bench_data" / "pdfs"
    target = args.bench_dir / "bench_data" / args.system
    work = args.bench_dir / "bench_data" / "_marker_work"
    work.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(p for p in pdf_root.rglob("*.pdf")
                  if not args.only or args.only in p.relative_to(pdf_root).parts)
    print(f"{len(pdfs)} pdfs -> {target}", flush=True)

    done = failed = 0
    started = time.perf_counter()
    for i, pdf in enumerate(pdfs, 1):
        rel = pdf.relative_to(pdf_root)
        out_md = target / rel.with_name(f"{rel.stem}_pg1_repeat1.md")
        if out_md.is_file():
            done += 1
            continue
        out_md.parent.mkdir(parents=True, exist_ok=True)
        stage = work / rel.stem
        shutil.rmtree(stage, ignore_errors=True)
        try:
            r = subprocess.run(
                [args.marker, str(pdf), "--output_format", "markdown",
                 "--output_dir", str(stage)],
                capture_output=True, text=True, timeout=900)
            if r.returncode != 0:
                raise RuntimeError((r.stderr or r.stdout)[-200:])
            produced = sorted(stage.rglob("*.md"))
            if not produced:
                raise RuntimeError("marker produced no markdown")
            text = produced[0].read_text(encoding="utf-8", errors="replace")
            out_md.write_text(FRONT_MATTER.sub("", text))
            done += 1
        except Exception as exc:
            failed += 1
            out_md.write_text("")
            print(f"  FAILED {rel}: {type(exc).__name__}: {exc}"[:180], flush=True)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        if i % 25 == 0 or i == len(pdfs):
            rate = (time.perf_counter() - started) / i
            print(f"  [{i}/{len(pdfs)}] {done} written, {failed} failed, "
                  f"{rate:.1f}s/pdf, ~{rate*(len(pdfs)-i)/60:.0f} min left", flush=True)
    print(f"\n{done} written, {failed} failed -> {target}", flush=True)


if __name__ == "__main__":
    main()
