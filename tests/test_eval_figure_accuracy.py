"""The figure gate is pinned to scientific and publisher-exported PDFs."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).parent.parent
SPEC = importlib.util.spec_from_file_location(
    "eval_figure_accuracy", ROOT / "scripts" / "eval_figure_accuracy.py"
)
figure_accuracy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(figure_accuracy)


def test_pinned_figure_accuracy_corpus_matches_sources_and_conversions():
    corpus_path = ROOT / "tests" / "figure_accuracy_corpus.json"

    report = figure_accuracy.evaluate(ROOT, corpus_path)

    assert figure_accuracy.check_corpus(corpus_path, report)
