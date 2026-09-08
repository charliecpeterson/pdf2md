"""Score the dropped-symbol check against an independent region reader.

    uv run python scripts/eval_symbol_precision.py OUT_DIR [--sample N]
                                                   [--json OUT] [--seed N]

`record_symbol_loss` raises an action wherever the pypdfium2 glyph layer shows a
Greek letter or math operator inside a block that the emitted text does not
carry. Every finding rests on that one reader, and a glyph pdfium resolves and
nobody else does would be indistinguishable from a symbol the engine dropped.

The truth side is poppler's `pdftotext`, cropped to the same region, as in
`eval_recall_precision.py`: a different PDF text stack, so a symbol both readers
show and the output lacks is missing on two independent accounts.

A flagged block counts as:
  confirmed  poppler shows a symbol the emitted text lacks
  partial    poppler shows some of the flagged symbols but not all
  refuted    poppler shows none of them -- the flag is pdfium's alone
  unusable   poppler read nothing for the region

Blocks with no finding are sampled too, as the control. Precision says how many
flags are real; without a control nothing says how much symbol loss goes
unflagged, which for a check this narrow is the likelier failure.

Symbol loss is recomputed from the source rather than read out of
`provenance.json`, so the harness scores the current check and not whatever ran
when the bundle was written.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from dataclasses import fields
from pathlib import Path

import pypdfium2 as pdfium

sys_path = Path(__file__).resolve().parent.parent / "src"
import sys

if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))

from pdf2md.enrich import _SYMBOL_DASHES, _SYMBOLS, GlyphIndex
from pdf2md.recall import record_symbol_loss
from pdf2md.schema import PROSE_TYPES, BBox, Block, BlockType

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_recall_precision import poppler_region


def _symbols(text: str) -> Counter:
    return Counter(c for c in _SYMBOLS.findall(text or "") if c not in _SYMBOL_DASHES)


def _rescored(version_dir: Path, source: Path) -> dict[str, dict]:
    """Per block id, the symbol loss as the *current* check computes it."""
    provenance = json.loads((version_dir / "provenance.json").read_text())
    block_fields = {f.name for f in fields(Block)}
    blocks = [
        Block(type=BlockType(r["type"]),
              bbox=BBox(**r["bbox"]) if r.get("bbox") else None,
              **{k: r[k] for k in r if k in block_fields and k not in ("type", "bbox")})
        for r in provenance["blocks"]
    ]
    out: dict[str, dict] = {}
    with GlyphIndex(source) as glyphs:
        for block in blocks:
            if block.type not in PROSE_TYPES or block.bbox is None:
                continue
            page_chars = glyphs.page_chars(block.page)
            if page_chars is None:
                continue
            block.extra.pop("glyph_symbols_lost", None)
            record_symbol_loss(block, page_chars)
            out[block.id] = block.extra.get("glyph_symbols_lost") or {}
    return out


def sample(version_dir: Path, per_doc: int, rng: random.Random) -> list[dict]:
    source = version_dir.parent / "source.pdf"
    if not source.is_file():
        return []
    provenance = json.loads((version_dir / "provenance.json").read_text())
    pdf = pdfium.PdfDocument(str(source))
    try:
        geometry = {}
        for page_no in range(1, len(pdf) + 1):
            box = pdf[page_no - 1].get_bbox()
            geometry[page_no] = ((box[0], box[1]), box[3] - box[1])
    finally:
        pdf.close()

    fresh = _rescored(version_dir, source)
    by_id = {b["id"]: b for b in provenance["blocks"]}
    flagged = [bid for bid, rec in fresh.items() if rec]
    clean = [bid for bid, rec in fresh.items() if not rec]
    rng.shuffle(clean)

    rows = []
    for bid in flagged + clean[:per_doc]:
        block = by_id.get(bid)
        if not block or not block.get("bbox"):
            continue
        origin, height = geometry.get(block["page"], ((0.0, 0.0), 792.0))
        text = poppler_region(source, block["page"], block["bbox"], origin, height)
        row = {
            "document": version_dir.parent.name,
            "block_id": bid,
            "page": block["page"],
            "flagged": bool(fresh[bid]),
            "pdfium_lost": fresh[bid].get("symbols", ""),
        }
        if text is None or not text.strip():
            row["verdict"] = "unusable"
            rows.append(row)
            continue
        missing = _symbols(text) - _symbols(block.get("text") or "")
        row["poppler_lost"] = "".join(sorted(missing.elements()))
        if row["flagged"]:
            # `χ (4) ×` -- each symbol is one character, any count follows in
            # parentheses. Taking the first character of each part is the only
            # parse that survives `×` being both a symbol and a plausible
            # separator, which is why the record does not use it as one.
            claimed = Counter()
            parts = fresh[bid]["symbols"].split()
            for index, part in enumerate(parts):
                if part.startswith("("):
                    continue
                count = 1
                if index + 1 < len(parts) and parts[index + 1].startswith("("):
                    count = int(parts[index + 1].strip("()"))
                claimed[part[0]] += count
            shared = sum((claimed & missing).values())
            row["verdict"] = (
                "confirmed" if shared and shared >= len(list(claimed.elements()))
                else "partial" if shared
                else "refuted"
            )
        else:
            # The control: a block the check left alone, where poppler still
            # shows a symbol the output lacks, is loss the check did not raise.
            row["verdict"] = "missed" if missing else "agreed"
        rows.append(row)
    return rows


def report(rows: list[dict]) -> None:
    flagged = [r for r in rows if r["flagged"]]
    control = [r for r in rows if not r["flagged"]]
    counts = Counter(r["verdict"] for r in flagged)
    judged = counts["confirmed"] + counts["partial"] + counts["refuted"]
    print(f"flagged blocks: {len(flagged)}  ({dict(counts)})")
    if judged:
        print(f"  precision (confirmed + partial): "
              f"{(counts['confirmed'] + counts['partial']) / judged:.2f}"
              f"   strict (confirmed only): {counts['confirmed'] / judged:.2f}")
    ccounts = Counter(r["verdict"] for r in control)
    cjudged = ccounts["missed"] + ccounts["agreed"]
    print(f"control blocks: {len(control)}  ({dict(ccounts)})")
    if cjudged:
        print(f"  symbol loss the check did not raise: {ccounts['missed'] / cjudged:.1%}")
    for row in flagged:
        if row["verdict"] in ("refuted", "unusable"):
            print(f"  {row['verdict']:9s} {row['document'][:34]:36s} {row['block_id']:14s} "
                  f"p{row['page']:<4d} pdfium={row['pdfium_lost']!r} "
                  f"poppler={row.get('poppler_lost', '')!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--sample", type=int, default=25,
                        help="control blocks per document")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    rows: list[dict] = []
    for doc in sorted(args.out_dir.iterdir()):
        versions = sorted(doc.glob("v*/provenance.json"))
        if versions:
            rows.extend(sample(versions[-1].parent, args.sample, rng))
    report(rows)
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    main()
