"""Column comparison separates agreements, refusals, and wrong lanes."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parent.parent
SPEC = importlib.util.spec_from_file_location(
    "eval_projection_column_corpus",
    ROOT / "scripts" / "eval_projection_column_corpus.py",
)
column_corpus = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(column_corpus)


def test_cell_outcome_checks_the_structural_column_run():
    panel = {"source_column": 4}
    rows = [[(10, 20), (30, 40)]]
    record = {
        "source_position": 0,
        "source_column": 5,
        "source_box": [25, 0, 45, 10],
    }

    assert column_corpus._cell_outcome(record, panel, rows) == (
        "agree", [30, 40],
    )
    record["source_box"] = [0, 0, 20, 10]
    assert column_corpus._cell_outcome(record, panel, rows) == (
        "disagree", [30, 40],
    )
    assert column_corpus._cell_outcome(record, panel, [None]) == (
        "tool_refused", None,
    )


def test_projection_column_corpus_pins_exact_three_outcome_totals():
    corpus = json.loads((ROOT / "tests" / "projection_column_corpus.json").read_text())

    assert corpus["expected"] == {
        "panels_in_corpus": 12,
        "panels_checked": 7,
        "panels_agree": 7,
        "panels_disagree": 0,
        "panels_tool_refused": 0,
        "panels_no_reference": 5,
        "cells_checked": 1089,
        "agree": 1089,
        "disagree": 0,
        "tool_refused": 0,
    }
