"""Panel-boundary comparison keeps cell identities explicit."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parent.parent
SPEC = importlib.util.spec_from_file_location(
    "eval_projection_panel_corpus",
    ROOT / "scripts" / "eval_projection_panel_corpus.py",
)
panel_corpus = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(panel_corpus)


def test_mismatched_records_compare_centers_with_assigned_panels():
    bounds = [(0, 100), (140, 300)]
    records = [
        {"id": "left", "panel": 0, "source_box": [20, 0, 40, 10]},
        {"id": "right", "panel": 1, "source_box": [180, 0, 220, 10]},
        {"id": "wrong", "panel": 1, "source_box": [50, 0, 70, 10]},
    ]

    assert panel_corpus._mismatched_records(bounds, records) == ["wrong"]


def test_projection_panel_corpus_pins_exact_three_outcome_totals():
    corpus = json.loads((ROOT / "tests" / "projection_panel_corpus.json").read_text())

    assert corpus["expected"] == {
        "tables_checked": 130,
        "key_cells_checked": 7886,
        "agree": 130,
        "disagree": 0,
        "tool_refused": 0,
        "no_reference": 1,
        "mismatched_key_cells": 0,
        "families": {
            "repeated_2_panel_continuation": {
                "tables": 7, "agree": 7, "disagree": 0, "tool_refused": 0,
                "no_reference": 0,
            },
            "repeated_2_panel_header": {
                "tables": 10, "agree": 10, "disagree": 0, "tool_refused": 0,
                "no_reference": 0,
            },
            "repeated_3_panel_header": {
                "tables": 3, "agree": 3, "disagree": 0, "tool_refused": 0,
                "no_reference": 0,
            },
            "single_panel": {
                "tables": 111, "agree": 110, "disagree": 0, "tool_refused": 0,
                "no_reference": 1,
            },
        },
    }
