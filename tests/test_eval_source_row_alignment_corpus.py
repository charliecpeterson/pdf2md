"""Controlled source-row cases distinguish safe alignment from required refusal."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPTS = Path(__file__).parent.parent / "scripts"
spec = importlib.util.spec_from_file_location(
    "eval_source_row_alignment_corpus",
    SCRIPTS / "eval_source_row_alignment_corpus.py",
)
alignment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alignment)


def _tsv(keys: list[str]) -> str:
    header = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\t"
        "top\twidth\theight\tconf\ttext"
    )
    rows = [header]
    for index, key in enumerate(keys, start=1):
        top = index * 20
        rows.append(f"5\t1\t1\t1\t{index}\t1\t10\t{top}\t20\t10\t99\t{key}")
        rows.append(f"5\t1\t1\t1\t{index}\t2\t100\t{top}\t30\t10\t99\t{index}.25")
    return "\n".join(rows) + "\n"


def _panel() -> dict:
    return {
        "keys": ["1", "2", "3", "4", "5", "6"],
        "key_bounds": [0, 50],
        "row_numeric_cells": [2, 2, 2, 2, 2, 2],
        "target_position": 2,
        "secondary_position": 4,
    }


def test_controlled_one_gap_restores_the_original_row_mapping():
    panel = _panel()
    tsv = _tsv(panel["keys"])
    baseline = alignment.recovery._panel_lines(tsv, tuple(panel["key_bounds"]))
    variant = next(item for item in alignment._variants(panel) if item["id"] == "one_gap")

    result = alignment._evaluate_variant(panel, tsv, baseline, variant)

    assert result["outcome"] == "agree"
    assert result["actual_method"] == "bracketed_one_gap"
    assert result["actual_inferred_position"] == 2
    assert result["mapping_matches"] is True


def test_one_gap_requires_matching_independent_projection_when_enabled():
    panel = _panel()
    tsv = _tsv(panel["keys"])
    baseline = alignment.recovery._panel_lines(tsv, tuple(panel["key_bounds"]))
    variant = next(item for item in alignment._variants(panel) if item["id"] == "one_gap")
    bands = [(index * 20, index * 20 + 11) for index in range(1, 7)]

    accepted = alignment._evaluate_variant(
        panel,
        tsv,
        baseline,
        variant,
        bands,
        require_projection=True,
    )
    shifted = list(bands)
    shifted[2] = (71, 82)
    refused = alignment._evaluate_variant(
        panel,
        tsv,
        baseline,
        variant,
        shifted,
        require_projection=True,
    )
    unavailable = alignment._evaluate_variant(
        panel,
        tsv,
        baseline,
        variant,
        require_projection=True,
    )

    assert accepted["outcome"] == "agree"
    assert refused["outcome"] == "tool_refused"
    assert refused["actual_reason"] == "projection_alignment_mismatch"
    assert unavailable["actual_reason"] == "projection_alignment_unavailable"


def test_comparator_exposes_wrong_mapping_and_unexpected_refusal():
    panel = _panel()
    tsv = _tsv(panel["keys"])
    baseline = alignment.recovery._panel_lines(tsv, tuple(panel["key_bounds"]))
    one_gap = next(item for item in alignment._variants(panel) if item["id"] == "one_gap")
    wrong_reference = {**one_gap, "inferred_position": 3}
    edge = next(item for item in alignment._variants(panel) if item["id"] == "edge_gap")
    unexpected_refusal = {
        **edge,
        "expected": "accept",
        "method": "bracketed_one_gap",
        "inferred_position": 0,
    }

    assert alignment._evaluate_variant(
        panel, tsv, baseline, wrong_reference
    )["outcome"] == "disagree"
    assert alignment._evaluate_variant(
        panel, tsv, baseline, unexpected_refusal
    )["outcome"] == "tool_refused"


def test_controlled_negative_cases_are_refused_for_the_pinned_reason():
    panel = _panel()
    tsv = _tsv(panel["keys"])
    baseline = alignment.recovery._panel_lines(tsv, tuple(panel["key_bounds"]))

    results = {
        variant["id"]: alignment._evaluate_variant(panel, tsv, baseline, variant)
        for variant in alignment._variants(panel)
        if variant["expected"] == "refuse"
    }

    assert {name: result["outcome"] for name, result in results.items()} == {
        "edge_gap": "agree",
        "two_gaps": "agree",
        "broken_anchor": "agree",
        "ambiguous_line": "agree",
        "below_exact_ratio": "agree",
    }
    assert results["ambiguous_line"]["actual_reason"] == "inferred_source_line_ambiguous"
    assert results["below_exact_ratio"]["actual_reason"] == "one_gap_alignment_unavailable"


def test_wrong_key_positions_leave_both_gap_anchors_untouched():
    positions = alignment._wrong_key_positions(20, 9)

    assert positions == [0, 1, 2]
    assert not set(positions) & {8, 9, 10}
