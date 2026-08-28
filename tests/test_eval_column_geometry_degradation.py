"""Every admitted column layout stays in a deterministic degradation gate."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "eval_column_geometry_degradation",
    SCRIPTS / "eval_column_geometry_degradation.py",
)
degradation = importlib.util.module_from_spec(SPEC)
try:
    SPEC.loader.exec_module(degradation)
finally:
    sys.path.pop(0)


def test_adaptive_binarization_preserves_dark_text_on_light_background():
    image = Image.new("L", (120, 60), 245)
    ImageDraw.Draw(image).rectangle((25, 20, 95, 40), fill=20)

    transformed = degradation.transform(image, "adaptive_binarization")

    assert transformed.size == image.size
    assert transformed.getpixel((60, 30)) == 0
    assert transformed.getpixel((5, 5)) == 255


def test_pinned_layout_gate_has_no_wrong_column_mapping(tmp_path):
    report = degradation.evaluate(
        ROOT, degradation.DEFAULT_SOURCES, tmp_path / "column-degradation"
    )

    assert degradation.check_corpus(ROOT, degradation.DEFAULT_CORPUS, report)
    assert report["layout_cases"] == 42
    assert report["rows_checked"] == 798
    assert report["methods"] == {
        "header_fixed": {"agree": 791, "disagree": 0, "tool_refused": 7},
        "consensus": {"agree": 655, "disagree": 0, "tool_refused": 143},
        "typed_consensus": {"agree": 655, "disagree": 0, "tool_refused": 143},
    }
    assert report["by_panel"]["grasp-mgiii-26"]["consensus"] == {
        "rows": 182,
        "agree": 182,
        "disagree": 0,
        "tool_refused": 0,
    }


def test_pinned_new_layout_gate_refuses_instead_of_mapping_wrong(tmp_path):
    sources = ROOT / "tests" / "column_geometry_new_layout_sources.json"
    corpus = ROOT / "tests" / "column_geometry_new_layout_corpus.json"
    report = degradation.evaluate(
        ROOT, sources, tmp_path / "column-new-layouts"
    )

    assert degradation.check_corpus(ROOT, corpus, report)
    assert report["panels"] == 4
    assert report["rows_checked"] == 245
    assert report["methods"] == {
        "header_fixed": {"agree": 245, "disagree": 0, "tool_refused": 0},
        "consensus": {"agree": 238, "disagree": 0, "tool_refused": 7},
        "typed_consensus": {
            "agree": 188,
            "disagree": 0,
            "tool_refused": 57,
        },
    }
    assert report["by_panel"]["attention-parser-long-labels"]["consensus"] == {
        "rows": 84,
        "agree": 77,
        "disagree": 0,
        "tool_refused": 7,
    }
