"""Score the reading-order check against poppler's own reading order.

    uv run python scripts/eval_reading_order_precision.py OUT_DIR
                                                          [--json OUT] [--quiet]

Reading order is the largest action category in the review output and the least
measured thing in the pipeline: its only previous number came from agreement
between two parsers, which needs MinerU installed. `pdftotext` (without
`-layout`) emits a page in the order poppler believes it reads, from a
different PDF stack, and that is an adjudicator available everywhere.

The check asserts that the emitted order is *not* the printed order. So for a
flagged page:

  confirmed  poppler also reads the blocks in a different order than pdf2md
             emitted them -- two independent readers disagree with the emission
  proven     the page has one column and a block sitting entirely below another
             is emitted before it. On one column the printed order IS top to
             bottom, so this needs no adjudicator and poppler cannot overrule it
  refuted    poppler reads them in exactly the emitted order, so nothing
             corroborates the claim
  unusable   too few blocks could be located in poppler's text to judge

`proven` exists because poppler is not independent of the failure it is being
asked to judge. Its reading order largely follows the PDF's content stream --
the same stream order an engine follows when it emits two lines the wrong way
round -- so on exactly the pages where the defect comes from stream order,
poppler agrees with the emission and refutes a correct finding. Measured over
this corpus, 14 refuted pages are provably out of printed order by the geometry
alone, which takes precision from 0.61 to 0.71. That is a floor either way: the
proof only inspects pairs adjacent in emission order, so a block moved several
positions is not caught and stays `refuted`.

Unflagged pages are scored the same way as a control: a page poppler reorders
that the check stayed silent on is a miss, and the two rates together say
whether the check is calibrated or merely noisy.

A block is located by its first few content words. Blocks whose anchor is not
found, or is ambiguous, are dropped rather than guessed -- a mislocated block
would manufacture exactly the disorder being measured.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pdf2md.reading_order import _longest_increasing  # noqa: E402

_FLOW = {"paragraph", "heading", "list"}
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
# Enough words to be unique on a page, few enough that a block whose tail the
# engine split differently still matches.
_ANCHOR_WORDS = 4
_MIN_BLOCKS = 4


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def poppler_page(pdf: Path, page: int) -> str | None:
    """The page as poppler reads it -- no `-layout`, so this is its reading
    order rather than its physical layout."""
    try:
        done = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), str(pdf), "-"],
            capture_output=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.decode(errors="replace") if done.returncode == 0 else None


def _anchor_at(haystack: list[str], anchor: list[str]) -> int | None:
    """Where `anchor` occurs in `haystack`, or None if absent or ambiguous."""
    if len(anchor) < _ANCHOR_WORDS:
        return None
    hits = [
        i for i in range(len(haystack) - len(anchor) + 1)
        if haystack[i:i + len(anchor)] == anchor
    ]
    return hits[0] if len(hits) == 1 else None


def score_page(blocks: list[dict], page_text: str) -> dict | None:
    """How many of this page's blocks poppler puts out of the emitted order."""
    haystack = _words(page_text)
    positions: list[int] = []
    for block in blocks:  # provenance lists blocks in emitted order
        where = _anchor_at(haystack, _words(block.get("text", ""))[:_ANCHOR_WORDS])
        if where is not None:
            positions.append(where)
    if len(positions) < _MIN_BLOCKS:
        return None
    keep = _longest_increasing(positions)
    return {"located": len(positions), "moves": len(positions) - len(keep)}


def evaluate(version_dir: Path) -> list[dict]:
    provenance = json.loads((version_dir / "provenance.json").read_text())
    source = version_dir.parent / "source.pdf"
    review = version_dir / "review.json"
    if not source.is_file() or not review.is_file():
        return []
    flagged = {
        item["page"] for item in json.loads(review.read_text())["items"]
        if item["reason"].startswith("reading order")
        and item.get("disposition") == "action_required"
    }

    by_page: dict[int, list[dict]] = {}
    for block in provenance["blocks"]:
        if block.get("type") in _FLOW and block.get("text", "").strip():
            by_page.setdefault(block["page"], []).append(block)

    rows = []
    for page, blocks in sorted(by_page.items()):
        if len(blocks) < _MIN_BLOCKS:
            continue
        text = poppler_page(source, page)
        if text is None:
            continue
        scored = score_page(blocks, text)
        if scored is None:
            rows.append({"document": version_dir.parent.name, "page": page,
                         "flagged": page in flagged, "verdict": "unusable"})
            continue
        verdict = "confirmed" if scored["moves"] else "refuted"
        if verdict == "refuted" and _inverted_single_column(blocks):
            verdict = "proven"
        rows.append({
            "document": version_dir.parent.name, "page": page,
            "flagged": page in flagged,
            "located": scored["located"], "moves": scored["moves"],
            "verdict": verdict,
        })
    return rows


def _inverted_single_column(blocks: list[dict]) -> bool:
    """Whether a one-column page emits a block that sits entirely below its
    predecessor -- printed order, established without a model or a threshold.

    Only pairs adjacent in emission order, and only bands that do not overlap at
    all, so nothing here rests on a judgement about what shares a line.

    The premise has to be tested, not assumed. `column_starts` drops a cluster
    holding less than a share of the page's blocks, so a two-column page whose
    right column carries two blocks is *reported* as one column -- Atkins page 41
    has nine blocks at x=43.5 and two at x=391.5 and comes back single-column.
    Comparing tops across that pair is comparing different columns, which proves
    nothing. So every block must actually begin at the one start; 9 of the 23
    pages this first credited did not."""
    from pdf2md.reading_order import (
        _COLUMN_TOLERANCE_PT, _flow_blocks, _top, column_starts,
    )
    from pdf2md.schema import BBox, Block, BlockType

    flow_input = [
        Block(id=b["id"], type=BlockType(b["type"]), text=b.get("text") or "",
              page=b["page"], bbox=BBox(**b["bbox"]))
        for b in blocks if b.get("bbox")
    ]
    emitted = {b["id"]: i for i, b in enumerate(blocks)}
    flow = _flow_blocks(flow_input, emitted)
    starts = column_starts([b.bbox for b in flow])
    if len(starts) != 1 or any(
        abs(b.bbox.x0 - starts[0]) > _COLUMN_TOLERANCE_PT for b in flow
    ):
        return False
    order = sorted(flow, key=lambda b: emitted[b.id])
    return any(
        _top(a.bbox) < min(b.bbox.y0, b.bbox.y1) for a, b in zip(order, order[1:])
    )


def report(rows: list[dict], quiet: bool) -> None:
    grid: Counter = Counter()
    for row in rows:
        grid[(row["flagged"], row["verdict"])] += 1
    print(f"{'':16s} {'poppler reorders':>17s} {'geometry proves':>16s} "
          f"{'poppler agrees':>15s} {'unusable':>9s}")
    for flagged, label in ((True, "check flagged"), (False, "check silent")):
        print(f"{label:16s} {grid[(flagged, 'confirmed')]:>17d} "
              f"{grid[(flagged, 'proven')]:>16d} "
              f"{grid[(flagged, 'refuted')]:>15d} {grid[(flagged, 'unusable')]:>9d}")
    upheld = grid[(True, "confirmed")] + grid[(True, "proven")]
    judged = upheld + grid[(True, "refuted")]
    missed = grid[(False, "confirmed")] + grid[(True, "confirmed")]
    if judged:
        print(f"\nprecision on flagged pages: {upheld / judged:.2f}"
              f"   (poppler alone: {grid[(True, 'confirmed')] / judged:.2f})")
    if missed:
        print(f"recall over pages poppler reorders: "
              f"{grid[(True, 'confirmed')] / missed:.2f}")
    if not quiet:
        bad = [r for r in rows if r["flagged"] and r["verdict"] == "refuted"]
        if bad:
            print("\nflagged pages poppler reads in exactly the emitted order:")
            for row in bad[:12]:
                print(f"  {row['document'][:26]:28s} p{row['page']:<5d} "
                      f"located={row['located']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    rows: list[dict] = []
    for doc in sorted(args.out_dir.iterdir()):
        versions = sorted(doc.glob("v*/provenance.json"),
                          key=lambda p: int(p.parent.name[1:]))
        if versions:
            rows.extend(evaluate(versions[-1].parent))
    report(rows, args.quiet)
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    main()
