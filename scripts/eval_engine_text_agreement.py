"""Compare two engines' block text for the same documents, and ask whether the
prose checks flag the blocks they read differently.

    uv run python scripts/eval_engine_text_agreement.py A_OUT_DIR B_OUT_DIR
                                                        [--json OUT] [--quiet]

The table audit and the reading-order check both got an unlabelled measurement
by running two parsers over one corpus. The prose side never did. Word recall,
lost diacritics and content conservation were all *changed* — conservation's
flags turned out to be markup counted on one side only, recall's to be its own
tokenization — and nothing independent has confirmed what survived.

Two parsers reading the same block differently means at least one is wrong.
Where they agree word for word, the text is very likely right. Neither is ground
truth, so this is concordance rather than precision.

Blocks are matched across engines by page and box overlap, because the ids are
each engine's own. A block matched to nothing is left out: that is a difference
about where the text is, not about what it says.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from eval_engine_table_agreement import _engine, _iou, _load  # noqa: E402

_PROSE = {"paragraph", "heading", "list", "caption", "footnote"}
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_SCRIPT_TAGS = re.compile(r"</?(?:sub|sup)>")
# The findings this measurement is about: everything the prose side reports.
_PROSE_REASONS = ("text layer recall", "content conservation", "diacritics lost")
_MIN_WORDS = 8
_MIN_IOU = 0.5


def _words(text: str) -> Counter:
    text = unicodedata.normalize("NFKC", _SCRIPT_TAGS.sub(" ", text)).lower()
    return Counter(_WORD.findall(text))


def _flagged_blocks(version_dir: Path) -> dict[str, list[str]]:
    review = version_dir / "review.json"
    if not review.is_file():
        return {}
    out: dict[str, list[str]] = defaultdict(list)
    for item in json.loads(review.read_text()).get("items", []):
        if item["reason"].startswith(_PROSE_REASONS):
            out[item["block_id"]].append(item["reason"].split(":")[0])
    return out


def _prose_blocks(document: dict) -> dict[int, list[dict]]:
    pages: dict[int, list[dict]] = defaultdict(list)
    for block in document.get("blocks", []):
        if block.get("type") in _PROSE and block.get("bbox") and block.get("text", "").strip():
            pages[block["page"]].append(block)
    return pages


def compare(left_dir: Path, right_dir: Path) -> dict:
    left, right = _load(left_dir), _load(right_dir)
    if left["source_sha256"] != right["source_sha256"]:
        raise SystemExit("different sources")
    flags = _flagged_blocks(left_dir)
    left_pages, right_pages = _prose_blocks(left), _prose_blocks(right)

    rows = []
    for page, blocks in sorted(left_pages.items()):
        remaining = list(right_pages.get(page, []))
        for block in blocks:
            best = max(
                ((_iou(block["bbox"], other["bbox"]), other) for other in remaining),
                key=lambda item: item[0],
                default=(0.0, None),
            )
            if best[0] < _MIN_IOU:
                continue
            remaining.remove(best[1])
            mine, theirs = _words(block["text"]), _words(best[1]["text"])
            total = sum((mine | theirs).values())
            if total < _MIN_WORDS:
                continue
            rows.append({
                "block_id": block["id"],
                "page": page,
                "agreement": sum((mine & theirs).values()) / total,
                "words": sum(mine.values()),
                "flags": flags.get(block["id"], []),
            })
    return {
        "source_sha256": left["source_sha256"],
        "left": {"dir": str(left_dir), "engine": _engine(left)},
        "right": {"dir": str(right_dir), "engine": _engine(right)},
        "blocks": rows,
    }


def report(results: list[dict], quiet: bool = False) -> None:
    grid: Counter = Counter()
    by_reason: Counter = Counter()
    for result in results:
        for row in result["blocks"]:
            differ = row["agreement"] < 1.0
            grid[(differ, bool(row["flags"]))] += 1
            for reason in row["flags"]:
                by_reason[(reason, differ)] += 1
            if not quiet and row["flags"] and not differ:
                print(f"{Path(result['left']['dir']).parent.name[:20]:20s} "
                      f"{row['block_id']:14s} p{row['page']:<4d} "
                      f"agreement=1.00 flags={','.join(row['flags'])}")
    print()
    print("prose findings vs engine disagreement about the text")
    print(f"{'':18s} {'check flagged':>14s} {'check silent':>13s}")
    print(f"{'engines differ':18s} {grid[(True, True)]:>14d} {grid[(True, False)]:>13d}")
    print(f"{'engines agree':18s} {grid[(False, True)]:>14d} {grid[(False, False)]:>13d}")
    if by_reason:
        print()
        print(f"{'FINDING':32s} {'on differing':>12s} {'on agreeing':>12s}")
        for reason in sorted({reason for reason, _ in by_reason}):
            print(f"{reason:32s} {by_reason[(reason, True)]:>12d} "
                  f"{by_reason[(reason, False)]:>12d}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    results = []
    for doc in sorted(args.left.iterdir()):
        other = args.right / doc.name
        if (doc / "v1/manifest.json").is_file() and (other / "v1/manifest.json").is_file():
            results.append(compare(doc / "v1", other / "v1"))
    report(results, quiet=args.quiet)
    if args.json:
        args.json.write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
