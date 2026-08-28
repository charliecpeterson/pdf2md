"""Column geometry consensus is conservative and source-reference checked."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw


ROOT = Path(__file__).parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "eval_column_geometry_methods", SCRIPTS / "eval_column_geometry_methods.py"
)
geometry = importlib.util.module_from_spec(SPEC)
try:
    SPEC.loader.exec_module(geometry)
finally:
    sys.path.pop(0)


def test_repeated_row_consensus_merges_fragments_inside_text_lane():
    image = Image.new("L", (300, 90), 255)
    draw = ImageDraw.Draw(image)
    row_bands = [(5, 20), (35, 50), (65, 80)]
    for top, bottom in row_bands:
        draw.rectangle((10, top, 30, bottom - 1), fill=0)
        draw.rectangle((105, top, 130, bottom - 1), fill=0)
        draw.rectangle((205, top, 220, bottom - 1), fill=0)
        draw.rectangle((235, top, 250, bottom - 1), fill=0)

    lanes, evidence, refusal = geometry.repeated_row_lanes(
        image, row_bands, (0, 300), 3
    )

    assert refusal is None
    assert lanes is not None
    assert len(lanes) == 3
    assert evidence["row_run_counts"] == [4, 4, 4]
    assert geometry._lane_rows(image, row_bands, lanes) == (3, 0)
    assert geometry._typed_rows(
        image, row_bands, [0, 300], lanes, ["numeric", "numeric", "text"]
    ) == (3, 0)


def test_persistent_vertical_rules_override_internal_word_gaps():
    image = Image.new("L", (300, 90), 255)
    draw = ImageDraw.Draw(image)
    row_bands = [(5, 20), (35, 50), (65, 80)]
    for top, bottom in row_bands:
        draw.rectangle((10, top + 2, 112, bottom - 3), fill=0)
        draw.rectangle((128, top + 2, 212, bottom - 3), fill=0)
        draw.rectangle((240, top + 2, 270, bottom - 3), fill=0)
        draw.rectangle((120, top, 121, bottom - 1), fill=0)
        draw.rectangle((220, top, 221, bottom - 1), fill=0)

    lanes, evidence, refusal = geometry.repeated_row_lanes(
        image, row_bands, (0, 300), 3
    )

    assert refusal is None
    assert lanes == [(0, 120), (120, 220), (220, 300)]
    assert evidence["method"] == "persistent_vertical_rules"
    assert evidence["rule_spans"] == [[120, 122], [220, 222]]


def test_pinned_column_geometry_comparison_recovers_mgiii_without_regression():
    report = geometry.evaluate(ROOT, geometry.DEFAULT_SOURCES)

    assert geometry.check_corpus(ROOT, geometry.DEFAULT_CORPUS, report)
    assert report["baseline_row_runs"] == {"exact": 87, "refused": 27}
    assert report["repeated_row_consensus"] == {"exact": 113, "refused": 1}
    assert report["typed_consensus"] == {"exact": 113, "refused": 1}
    assert report["selected_cells"] == {
        "checked": 56,
        "agree": 56,
        "disagree": 0,
        "tool_refused": 0,
    }
    assert report["pdf_glyph_reference"] == {
        "checked": 4184,
        "agree": 4184,
        "disagree": 0,
        "no_reference_panels": 1,
    }
    panels = {panel["id"]: panel for panel in report["per_panel"]}
    assert panels["grasp-mgiii-26"]["baseline"]["refused"] == 26
    assert panels["grasp-mgiii-26"]["consensus"]["exact"] == 26
    assert panels["fischer-stability-table-ii"]["consensus"]["refused"] == 1


def test_column_geometry_rejects_source_hash_drift(tmp_path):
    sources = json.loads(geometry.DEFAULT_SOURCES.read_text())
    sources["artifacts"]["alignment_report"]["sha256"] = "0" * 64
    sources_path = tmp_path / "sources.json"
    sources_path.write_text(json.dumps(sources))

    with pytest.raises(ValueError, match="alignment_report"):
        geometry.evaluate(ROOT, sources_path)
