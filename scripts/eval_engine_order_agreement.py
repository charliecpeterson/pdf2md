"""Compare two engines' block order for the same document, and ask whether the
reading-order check flags the pages they order differently.

    uv run python scripts/eval_engine_order_agreement.py A_OUT_DIR B_OUT_DIR
                                                         [--json OUT] [--quiet]

The table audit got an unlabelled measurement this way: run two parsers over one
corpus and see whether the check fires where they disagree. The reading-order
check never got one — it rests on two pages verified by hand against printed
reference numbers, which is not a basis for trusting it on a corpus.

Two parsers ordering the same page differently means at least one is wrong. Where
they agree, the order is very likely right. Neither is ground truth, so this is
concordance rather than precision; it is still sixty pages nobody chose over two
that were.

Blocks are matched across engines by page and box overlap, because the ids are
each engine's own. Only pages where most blocks match are compared: a page the
two read into different regions has no shared order to disagree about.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from pdf2md.reading_order import _longest_increasing
from pdf2md.schema import BlockType

# Reuse the table differential's matcher: same problem, same solution.
import sys

sys.path.insert(0, str(Path(__file__).parent))
from eval_engine_table_agreement import _engine, _iou, _load  # noqa: E402

_FLOW = {BlockType.PARAGRAPH.value, BlockType.HEADING.value, BlockType.LIST.value}
_MIN_MATCH_SHARE = 0.6
_MIN_BLOCKS = 4


def _flow_blocks(document: dict) -> dict[int, list[dict]]:
    pages: dict[int, list[dict]] = defaultdict(list)
    for index, block in enumerate(document.get("blocks", [])):
        if block.get("type") in _FLOW and block.get("bbox") and block.get("text", "").strip():
            pages[block["page"]].append({**block, "order": index})
    return pages


def _pair(left: list[dict], right: list[dict]) -> list[tuple[dict, dict]]:
    remaining = list(right)
    pairs = []
    for block in left:
        best = max(
            ((_iou(block["bbox"], other["bbox"]), other) for other in remaining),
            key=lambda item: item[0],
            default=(0.0, None),
        )
        if best[0] >= 0.5:
            remaining.remove(best[1])
            pairs.append((block, best[1]))
    return pairs


def compare(left_dir: Path, right_dir: Path) -> dict:
    left, right = _load(left_dir), _load(right_dir)
    if left["source_sha256"] != right["source_sha256"]:
        raise SystemExit("different sources")
    # Only pages carrying an order finding. `reading_order_pages` also records
    # split lines, which is informational and says nothing about order.
    flagged = {
        int(page) for page, detail in (
            json.loads((left_dir / "profile.json").read_text())
            .get("reading_order_pages") or {}
        ).items()
        if "geometry" in detail or "numbering" in detail
    }
    left_pages, right_pages = _flow_blocks(left), _flow_blocks(right)

    rows = []
    for page in sorted(left_pages):
        pairs = _pair(left_pages[page], right_pages.get(page, []))
        blocks = len(left_pages[page])
        if blocks < _MIN_BLOCKS or len(pairs) < _MIN_MATCH_SHARE * blocks:
            continue
        pairs.sort(key=lambda item: item[0]["order"])
        sequence = [other["order"] for _, other in pairs]
        misplaced = len(sequence) - len(_longest_increasing(sequence))
        rows.append({
            "page": page,
            "blocks": blocks,
            "matched": len(pairs),
            "misplaced": misplaced,
            "flagged": page in flagged,
        })
    return {
        "source_sha256": left["source_sha256"],
        "left": {"dir": str(left_dir), "engine": _engine(left)},
        "right": {"dir": str(right_dir), "engine": _engine(right)},
        "pages": rows,
    }


def report(results: list[dict], quiet: bool = False) -> None:
    grid: Counter = Counter()
    for result in results:
        for row in result["pages"]:
            grid[(row["misplaced"] > 0, row["flagged"])] += 1
            if not quiet and (row["misplaced"] or row["flagged"]):
                print(f"{Path(result['left']['dir']).parent.name[:22]:22s} "
                      f"p{row['page']:<4d} blocks={row['blocks']:<4d} "
                      f"matched={row['matched']:<4d} engines_differ_by={row['misplaced']:<3d} "
                      f"{'FLAGGED' if row['flagged'] else ''}")
    print()
    print("reading-order findings vs engine disagreement about order")
    print(f"{'':18s} {'check flagged':>14s} {'check silent':>13s}")
    print(f"{'engines differ':18s} {grid[(True, True)]:>14d} {grid[(True, False)]:>13d}")
    print(f"{'engines agree':18s} {grid[(False, True)]:>14d} {grid[(False, False)]:>13d}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("left", type=Path, help="out dir of one engine's bundles")
    parser.add_argument("right", type=Path, help="out dir of the other's")
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    results = []
    for doc in sorted(args.left.iterdir()):
        other = args.right / doc.name
        if not (doc / "v1/manifest.json").is_file():
            continue
        if not (other / "v1/manifest.json").is_file():
            continue
        results.append(compare(doc / "v1", other / "v1"))
    report(results, quiet=args.quiet)
    if args.json:
        args.json.write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
