"""The pinned GRASP manual measures part and chapter file boundaries."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).parent.parent
SPEC = importlib.util.spec_from_file_location(
    "eval_book_splitting", ROOT / "scripts" / "eval_book_splitting.py"
)
book_splitting = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(book_splitting)


def test_pinned_book_split_corpus_matches_source_and_conversion():
    corpus_path = ROOT / "tests" / "book_split_corpus.json"

    report = book_splitting.evaluate(ROOT, corpus_path)

    assert book_splitting.check_corpus(corpus_path, report)
