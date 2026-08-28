"""Measure source-labelled book file boundaries against the embedded outline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from pdf2md.bookmarks import read_bookmarks
from pdf2md.emit import _file_units
from pdf2md.schema import Block, BlockType
from pdf2md.structure import build_structure


ROOT = Path(__file__).parent.parent
DEFAULT_CORPUS = ROOT / "tests" / "book_split_corpus.json"
DEFAULT_REPORT = ROOT / "out" / "reviews" / "book-splitting-v1.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate(root: Path, corpus_path: Path) -> dict:
    corpus = json.loads(corpus_path.read_text())
    source = root / corpus["source"]["path"]
    provenance_path = root / corpus["conversion"]["provenance_path"]
    if _sha256(source) != corpus["source"]["sha256"]:
        raise ValueError("book source hash mismatch")
    if _sha256(provenance_path) != corpus["conversion"]["provenance_sha256"]:
        raise ValueError("book conversion provenance hash mismatch")

    provenance = json.loads(provenance_path.read_text())
    blocks = [
        Block(
            id=record["id"],
            type=BlockType(record["type"]),
            text=record["text"],
            page=record["page"],
        )
        for record in provenance["blocks"]
    ]
    structure = build_structure(
        blocks,
        read_bookmarks(source),
        title=source.stem,
        page_count=provenance["page_count"],
    )
    units = _file_units(structure.root, structure.split_depth)
    boundaries = [section.page_start for section, _ in units]
    ends = boundaries[1:] + [provenance["page_count"] + 1]
    measurement = {
        "top_level_bookmarks": len(structure.root.children),
        "split_depth": structure.split_depth,
        "part_opener_files": [
            section.title for section, _ in units if section.depth == 1
        ],
        "chapter_files": [
            section.title for section, _ in units if section.depth == 2
        ],
        "file_count": len(units),
        "largest_file_page_span": max(
            end - start for start, end in zip(boundaries, ends, strict=True)
        ),
    }
    return {
        "schema_version": 1,
        "corpus_sha256": _sha256(corpus_path),
        "source_sha256": _sha256(source),
        "provenance_sha256": _sha256(provenance_path),
        "measurement": measurement,
    }


def check_corpus(corpus_path: Path, report: dict) -> bool:
    corpus = json.loads(corpus_path.read_text())
    if report["measurement"] != corpus["expected"]:
        raise ValueError("book splitting result differs from the frozen corpus")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    report = evaluate(ROOT, args.corpus)
    if args.check:
        check_corpus(args.corpus, report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    measured = report["measurement"]
    print(
        f"book splitting: {measured['top_level_bookmarks']} parts, "
        f"{len(measured['chapter_files'])} chapters, {measured['file_count']} files"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
