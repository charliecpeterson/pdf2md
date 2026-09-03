"""Convert olmOCR-bench's PDFs with pdf2md and lay the Markdown out for its scorer.

    uv run python scripts/run_olmocr_bench.py BENCH_DIR [--limit N] [--only SUBSET]
    # then, in an environment with olmocr installed:
    #   python -m olmocr.bench.benchmark --dir BENCH_DIR/bench_data

Every measurement in this project is self-generated: its own label sets, its own
corpora, poppler standing in as an adjudicator. olmOCR-bench is the first outside
one. It scores five things -- text present, header/footer text absent, the
relative order of two spans, table cell neighbours, and math formula layout --
over 1,403 PDFs and about 7,000 unit tests, which is exactly the part of pdf2md
that is not figures. It has no figure or chart tests at all.

Read the score for what it is. pdf2md's default engine is Docling, reported at
roughly 50 on this benchmark against MinerU's ~73 and Marker 2's ~76, so a run
mostly measures Docling. What it can say that nothing else has is how much
pdf2md's enrichment layer -- ligature repair, font-decode refill, script
recovery, the glyph table rebuild -- moves that number. Run `--engine mineru` for
the other half of the comparison.

WHAT IS STRIPPED, decided before any score was seen
---------------------------------------------------
pdf2md emits two things beside the page's content that a text-absence test would
rightly punish, and both already have a definition in `conservation.py` because
the content-conservation audit has to make the same distinction:

  `_PDF2MD_MARKER`   the `> **[pdf2md: ...]**` review blockquotes
  `_EMITTED_NAV`     the `*[pdf2md] table source:*` navigation lines

Those two, plus the YAML front matter and the `<!-- page N -->` anchors, come
out. The page anchors are pdf2md's own, not text the page printed, and a
header/footer absence test looking for "page 1" would match one and score it as
a failure the tool did not commit. Nothing else does -- not the
HTML table fallback, not LaTeX, not image links, not headings. `semantic_output`
would have removed HTML tags and TeX commands too, which would have quietly
destroyed the table and math tests; reusing it wholesale was the obvious wrong
move here.

A book that splits into several files is concatenated in `document_map` order.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from pdf2md.config import Config  # noqa: E402
from pdf2md.conservation import _EMITTED_NAV, _PDF2MD_MARKER  # noqa: E402
from pdf2md.pipeline import convert_file  # noqa: E402

_FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
# pdf2md's own page anchors. Not printed text, and a header/footer absence test
# looking for "page 1" would otherwise match one.
_PAGE_ANCHOR = re.compile(r"^<!-- page \d+ -->$\n?", re.MULTILINE)
_SYSTEM = "pdf2md"


def bench_markdown(version_dir: Path) -> str:
    """The bundle's Markdown as the benchmark should see it."""
    order = [version_dir / "document.md"]
    document_map = version_dir / "outline.json"
    if document_map.is_file():
        files = [f["path"] if isinstance(f, dict) else f
                 for f in (json.loads(document_map.read_text()).get("markdown_files") or [])]
        if files:
            order = [version_dir / f for f in files]
    parts = []
    for path in order:
        if not path.is_file():
            continue
        text = _FRONT_MATTER.sub("", path.read_text())
        text = _PDF2MD_MARKER.sub("", text)
        text = _EMITTED_NAV.sub("", text)
        text = _PAGE_ANCHOR.sub("", text)
        parts.append(text.strip())
    return "\n\n".join(p for p in parts if p) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bench_dir", type=Path, help="the olmOCR-bench checkout")
    parser.add_argument("--limit", type=int, default=0, help="stop after N pdfs (a smoke run)")
    parser.add_argument("--only", default="", help="only this subset directory, e.g. multi_column")
    parser.add_argument("--out", type=Path, default=None, help="pdf2md bundles (default: a temp dir)")
    parser.add_argument("--engine", default="docling")
    parser.add_argument("--no-formula", action="store_true",
                        help="skip formula enrichment; much faster, and the math tests will show it")
    args = parser.parse_args()

    pdf_root = args.bench_dir / "bench_data" / "pdfs"
    if not pdf_root.is_dir():
        raise SystemExit(f"no pdfs under {pdf_root} — download the dataset first")
    target = args.bench_dir / "bench_data" / _SYSTEM
    bundles = args.out or (args.bench_dir / "bench_data" / "_pdf2md_bundles")

    pdfs = sorted(p for p in pdf_root.rglob("*.pdf")
                  if not args.only or args.only in p.relative_to(pdf_root).parts)
    if args.limit:
        pdfs = pdfs[: args.limit]
    print(f"{len(pdfs)} pdfs -> {target}")

    config = Config(engine=args.engine,
                    do_formula_enrichment=not args.no_formula)
    done = failed = 0
    started = time.perf_counter()
    for i, pdf in enumerate(pdfs, 1):
        rel = pdf.relative_to(pdf_root).with_suffix(".md")
        out_md = target / rel
        if out_md.is_file():
            done += 1
            continue
        out_md.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = convert_file(pdf, output_root=bundles, config=config)
            if result.failed:
                raise RuntimeError(result.error or "conversion failed")
            out_md.write_text(bench_markdown(Path(result.out_dir)))
            done += 1
        except Exception as exc:  # noqa: BLE001 - one bad pdf must not stop 1,400
            failed += 1
            out_md.write_text("")  # an empty candidate scores as a miss, not a crash
            print(f"  FAILED {rel}: {type(exc).__name__}: {exc}")
        if i % 25 == 0 or i == len(pdfs):
            rate = (time.perf_counter() - started) / i
            print(f"  [{i}/{len(pdfs)}] {done} written, {failed} failed, "
                  f"{rate:.1f}s/pdf, ~{rate * (len(pdfs) - i) / 60:.0f} min left")
    print(f"\n{done} written, {failed} failed -> {target}")
    print(f"score with:  python -m olmocr.bench.benchmark --dir {args.bench_dir / 'bench_data'}")


if __name__ == "__main__":
    main()
