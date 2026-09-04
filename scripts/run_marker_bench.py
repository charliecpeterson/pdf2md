"""Convert the olmOCR-bench PDFs with Marker's own CLI, named for the scorer.

Measures Marker standalone -- it bounds the headroom an engine adapter could
reach before anyone writes one. Same machine, same scorer, same candidate naming
as the pdf2md run, so the per-subset numbers are comparable.

Runs Marker's batch mode once per subset rather than per file: `marker_single`
reloads its models on every invocation, which costs more than the conversion. Per
subset rather than over the whole tree because `old_scans` uses bare numeric
stems that would collide with other subsets in one flat output directory.
"""
import argparse
import re
import subprocess
import time
from pathlib import Path

FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bench_dir", type=Path)
    ap.add_argument("--marker", default="marker")
    ap.add_argument("--system", default="marker")
    ap.add_argument("--only", default="")
    ap.add_argument("--mode", default="balanced")
    args = ap.parse_args()

    pdf_root = args.bench_dir / "bench_data" / "pdfs"
    target = args.bench_dir / "bench_data" / args.system
    work = args.bench_dir / "bench_data" / "_marker_work"

    subsets = sorted(d for d in pdf_root.iterdir()
                     if d.is_dir() and (not args.only or d.name == args.only))
    print(f"{len(subsets)} subsets -> {target}", flush=True)

    for sub in subsets:
        pdfs = sorted(sub.glob("*.pdf"))
        out = work / sub.name
        out.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        r = subprocess.run(
            [args.marker, str(sub), "--output_format", "markdown",
             "--output_dir", str(out), "--mode", args.mode, "--skip_existing"],
            capture_output=True, text=True)
        elapsed = time.perf_counter() - started
        if r.returncode != 0:
            print(f"  {sub.name}: marker exited {r.returncode}: "
                  f"{(r.stderr or r.stdout)[-300:]}", flush=True)

        written = missing = 0
        for pdf in pdfs:
            dest = target / sub.name / f"{pdf.stem}_pg1_repeat1.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            produced = sorted((out / pdf.stem).glob("*.md")) if (out / pdf.stem).is_dir() else []
            if produced:
                dest.write_text(FRONT_MATTER.sub("", produced[0].read_text(
                    encoding="utf-8", errors="replace")))
                written += 1
            else:
                dest.write_text("")
                missing += 1
        print(f"  {sub.name}: {written} written, {missing} missing, "
              f"{elapsed:.0f}s ({elapsed/max(1,len(pdfs)):.1f}s/pdf)", flush=True)


if __name__ == "__main__":
    main()
