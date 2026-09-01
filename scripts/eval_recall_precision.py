"""Score the per-block word-recall check against an independent region reader.

    uv run python scripts/eval_recall_precision.py OUT_DIR [--per-band N]
                                                   [--json OUT] [--seed N]

Every other check in this project has been measured. Word recall has not, and
it is now the highest-volume finding by a wide margin, so its precision decides
whether the rest of the review output is readable or buried. The GRASP2018
manual made the risk concrete: 146 of its 160 findings were artifacts of the
metric's own tokenization, and the 14 real ones were invisible underneath.

The truth side is poppler's `pdftotext`, cropped to the same region. It is a
different PDF text stack from the pypdfium2 layer the check itself reads, so a
word both readers see and the output lacks is missing on two independent
accounts; a word only pypdfium2 sees is not evidence of anything. That is the
same standard the table-audit labels were held to, mechanized because a recall
sample is far too large to label by hand.

A flagged block counts as:
  confirmed  poppler also shows content words absent from the emitted text
  refuted    poppler shows nothing the output lacks -- the flag is the
             metric's own artifact
  unusable   poppler read nothing for the region, or cannot read the page at
             all: a font whose f-ligatures have no ToUnicode entry makes
             poppler drop them outright (it renders `defining` as `dening`),
             and a reader that silently loses characters is biased toward
             refuting, because it sees less and so finds less missing. Those
             pages are refused rather than scored -- the same standard the
             checks themselves are held to.

Blocks are sampled per ratio band so the bands with the most volume do not
drown out the extremes -- the 0.8-0.9 band alone holds 54% of all findings.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pypdfium2 as pdfium  # noqa: E402

from pdf2md.enrich import _recall_words  # noqa: E402

_BANDS = ((0.0, 0.5), (0.5, 0.8), (0.8, 0.9))
# A word only worth counting as "missing" if it carries content. Single letters
# are where the two readers legitimately disagree (initials, axis labels, a
# stray list marker), and they dominate any raw difference.
_MIN_WORD = 2
_STOP = {"the", "a", "an", "of", "and", "or", "in", "to", "is", "are", "for",
         "on", "with", "as", "by", "at", "be", "it", "that", "this", "from"}
# The broken-font signature: TeX puts its f-ligatures in the C0 control range
# with no ToUnicode entry. pypdfium2 surfaces the raw bytes (pdf2md maps them
# back to letters); poppler drops them. A page carrying them cannot be judged
# by comparing the two readers.
_LIGATURE_BYTE = re.compile(r"[\x02\x1b-\x1f]")


def _fold(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", word)
                   if not unicodedata.combining(c))


def _content(words: list[str]) -> Counter:
    return Counter(_fold(w) for w in words if len(w) >= _MIN_WORD and w not in _STOP)


def poppler_region(pdf_path: Path, page: int, bbox: dict, origin: tuple[float, float],
                   height: float) -> str | None:
    """`pdftotext` over one region. Coordinates are top-left pixels at 72 dpi,
    relative to the page's visible box -- hence the origin subtraction and the
    Y flip, the same translation `render.py` makes for the raster."""
    x0, x1 = sorted((bbox["x0"], bbox["x1"]))
    y0, y1 = sorted((bbox["y0"], bbox["y1"]))
    left = x0 - origin[0]
    top = height - (y1 - origin[1])
    width, tall = x1 - x0, y1 - y0
    if width <= 1 or tall <= 1:
        return None
    cmd = ["pdftotext", "-f", str(page), "-l", str(page), "-r", "72",
           "-x", str(int(left)), "-y", str(int(top)),
           "-W", str(int(width) + 1), "-H", str(int(tall) + 1),
           str(pdf_path), "-"]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout.decode(errors="replace") if res.returncode == 0 else None


def sample(version_dir: Path, per_band: int, rng: random.Random) -> list[dict]:
    provenance = json.loads((version_dir / "provenance.json").read_text())
    source = version_dir.parent / "source.pdf"
    if not source.is_file():
        return []
    pdf = pdfium.PdfDocument(str(source))
    try:
        geometry = {}
        degraded: set[int] = set()
        for page_no in range(1, len(pdf) + 1):
            page = pdf[page_no - 1]
            box = page.get_bbox()
            geometry[page_no] = ((box[0], box[1]), box[3] - box[1])
            if _LIGATURE_BYTE.search(page.get_textpage().get_text_bounded()):
                degraded.add(page_no)
    finally:
        pdf.close()

    banded: dict[tuple[float, float], list[dict]] = {b: [] for b in _BANDS}
    for block in provenance["blocks"]:
        rec = (block.get("extra") or {}).get("glyph_word_recall")
        if not rec or not rec["total"] or not block.get("bbox"):
            continue
        ratio = rec["matched"] / rec["total"]
        for band in _BANDS:
            if band[0] <= ratio < band[1]:
                banded[band].append(block)
                break

    out = []
    for band, blocks in banded.items():
        rng.shuffle(blocks)
        for block in blocks[:per_band]:
            origin, height = geometry.get(block["page"], ((0.0, 0.0), 792.0))
            text = poppler_region(source, block["page"], block["bbox"], origin, height)
            rec = block["extra"]["glyph_word_recall"]
            row = {
                "document": version_dir.parent.name,
                "block_id": block["id"],
                "page": block["page"],
                "band": f"{band[0]}-{band[1]}",
                "recall": rec["matched"] / rec["total"],
                "total": rec["total"],
            }
            if block["page"] in degraded:
                row["verdict"] = "unusable"
                row["reason"] = "poppler drops this font's ligatures"
            elif text is None or not text.strip():
                row["verdict"] = "unusable"
            else:
                missing = _content(_recall_words(text)) - _content(_recall_words(block["text"]))
                row["verdict"] = "confirmed" if missing else "refuted"
                row["missing"] = sorted(missing)[:10]
            out.append(row)
    return out


def report(rows: list[dict]) -> None:
    by_band: dict[str, Counter] = {}
    for row in rows:
        by_band.setdefault(row["band"], Counter())[row["verdict"]] += 1
    print(f"{'band':12s} {'confirmed':>10s} {'refuted':>8s} {'unusable':>9s} {'precision':>10s}")
    total = Counter()
    for band in sorted(by_band):
        c = by_band[band]
        total += c
        judged = c["confirmed"] + c["refuted"]
        p = f"{c['confirmed'] / judged:.2f}" if judged else "n/a"
        print(f"{band:12s} {c['confirmed']:>10d} {c['refuted']:>8d} {c['unusable']:>9d} {p:>10s}")
    judged = total["confirmed"] + total["refuted"]
    p = f"{total['confirmed'] / judged:.2f}" if judged else "n/a"
    print(f"{'ALL':12s} {total['confirmed']:>10d} {total['refuted']:>8d} "
          f"{total['unusable']:>9d} {p:>10s}")
    refuted = [r for r in rows if r["verdict"] == "refuted"]
    if refuted:
        print("\nflags poppler does not corroborate (the metric's own artifacts):")
        for row in refuted[:10]:
            print(f"  {row['document'][:24]:26s} {row['block_id']:14s} "
                  f"p{row['page']:<4d} recall={row['recall']:.2f} n={row['total']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--per-band", type=int, default=12)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    rows: list[dict] = []
    for doc in sorted(args.out_dir.iterdir()):
        versions = sorted(doc.glob("v*/provenance.json"))
        if versions:
            rows.extend(sample(versions[-1].parent, args.per_band, rng))
    report(rows)
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    main()
