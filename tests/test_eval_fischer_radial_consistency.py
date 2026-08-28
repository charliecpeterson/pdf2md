"""Radial scientific checks consume structural refusals as well as numeric CSV rows."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location(
    "eval_fischer_radial_consistency",
    ROOT / "scripts" / "eval_fischer_radial_consistency.py",
)
radial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(radial)


def test_trapezoid_recomputes_normalization_and_moments():
    points = [(0.0, 0.0), (1.0, 1.0), (2.0, 0.0)]

    assert radial._trapezoid(points, 0) == 1.0
    assert radial._trapezoid(points, -1) == 1.0
    assert radial._trapezoid(points, 1) == 1.0
    assert radial._trapezoid(points, 2) == 1.0


def test_structurally_refused_panel_refuses_each_expected_orbital(tmp_path):
    table_dir = tmp_path / "data" / "tables"
    table_dir.mkdir(parents=True)
    (table_dir / "page_020_panels.json").write_text(json.dumps({
        "representation": "repeated_panels",
        "panels": [
            {
                "metadata": {"atomic_number": 9},
                "columns": ["RADIUS", "1S", "2S", "2P"],
                "refused_rows": [
                    {"reason": "ambiguous_shifted_panel_boundary"},
                    {"reason": "ambiguous_shifted_panel_boundary"},
                ],
            },
            {
                "metadata": {"atomic_number": 10},
                "columns": ["RADIUS", "1S", "2S", "2P"],
                "refused_rows": [],
            },
        ],
    }))

    issues = radial._normalization_refusals(
        tmp_path,
        {(9, "1s"), (9, "2s"), (9, "2p"), (10, "1s")},
    )

    assert issues == {
        (9, "1s"): ["normalized_ambiguous_shifted_panel_boundary"],
        (9, "2s"): ["normalized_ambiguous_shifted_panel_boundary"],
        (9, "2p"): ["normalized_ambiguous_shifted_panel_boundary"],
    }


def test_pinned_radial_cross_check_supports_or_refuses_every_orbital():
    report = radial.evaluate(radial.DEFAULT_VERSION, radial.DEFAULT_CASES)

    assert radial.check_corpus(ROOT, radial.DEFAULT_CORPUS, report)
    assert report["outcomes"] == {
        "scientifically_consistent": 17,
        "disagree": 0,
        "tool_refused": 6,
    }
    refused = [
        record for record in report["records"]
        if record["outcome"] == "tool_refused"
    ]
    assert {(record["atomic_number"], record["orbital"]) for record in refused} == {
        (9, "1s"), (9, "2s"), (9, "2p"),
        (10, "1s"), (10, "2s"), (10, "2p"),
    }
    assert all(
        any(issue.startswith("normalized_") for issue in record["issues"])
        for record in refused
    )
