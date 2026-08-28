"""The fixed-font diagnostic learns from reader agreement, not gold labels."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_spec = importlib.util.spec_from_file_location(
    "eval_fixed_font_glyphs",
    Path(__file__).parent.parent / "scripts" / "eval_fixed_font_glyphs.py",
)
glyphs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(glyphs)


def _draw_value(path: Path, value: str) -> None:
    font = ImageFont.load_default(size=32)
    image = Image.new("L", (len(value) * 30 + 20, 55), 255)
    draw = ImageDraw.Draw(image)
    for index, character in enumerate(value):
        draw.text((10 + index * 30, 8), character, fill=0, font=font)
    image.save(path)


def _draw_value_with_rule(path: Path, value: str) -> None:
    font = ImageFont.load_default(size=32)
    image = Image.new("L", (len(value) * 30 + 20, 65), 255)
    draw = ImageDraw.Draw(image)
    draw.text((10, 8), value, fill=0, font=font)
    draw.rectangle((0, 60, image.width, 64), fill=0)
    image.save(path)


def test_glyph_atlas_ranks_candidates_without_training_on_expected(tmp_path):
    source_sha256 = "a" * 64
    agreed = ["0.0209", "0.2524", "0.0250", "0.0709"]
    records = []
    crops = []
    for row, value in enumerate(agreed):
        path = f"agreed-{row}.png"
        _draw_value(tmp_path / path, value)
        records.append({
            "source_sha256": source_sha256,
            "block_id": "#/table",
            "row": row,
            "column": 0,
            "expected": "deliberately-unused-gold",
            "actual": value,
            "reference_actual": value,
        })
        crops.append({
            "source_sha256": source_sha256,
            "block_id": "#/table",
            "source_row": row,
            "source_column": 0,
            "path": path,
        })

    _draw_value(tmp_path / "disagreement.png", "0.0250")
    records.append({
        "source_sha256": source_sha256,
        "block_id": "#/table",
        "row": 10,
        "column": 0,
        "expected": "0.0250",
        "actual": "0.0750",
        "reference_actual": "0.0250",
    })
    crops.append({
        "source_sha256": source_sha256,
        "block_id": "#/table",
        "source_row": 10,
        "source_column": 0,
        "path": "disagreement.png",
    })
    report_path = tmp_path / "report.json"
    manifest_path = tmp_path / "crops.json"
    report_path.write_text(json.dumps({"records": records}))
    manifest_path.write_text(json.dumps({"crops": crops}))

    result = glyphs.evaluate(report_path, manifest_path, source_sha256)

    assert result["atlas_cells"] == 4
    assert result["reader_disagreements"] == {
        "checked": 1,
        "ranked": 1,
        "refused": 0,
        "preferred_correct": 1,
        "agree": 1,
        "disagree": 0,
        "tool_refused": 0,
    }
    assert result["rankings"][0]["preferred"] == "0.0250"
    assert result["rankings"][0]["outcome"] == "agree"
    assert sum(result["rankings"][0]["jackknife_preferences"].values()) == 4
    assert isinstance(result["rankings"][0]["jackknife_stable"], bool)
    assert (
        result["jackknife_stability"]["stable"]
        + result["jackknife_stability"]["unstable"]
        == result["jackknife_stability"]["checked"]
    )
    assert result["leave_one_out_margin"]["cells"] == 2
    assert result["leave_one_out_margin"]["minimum"] > 0


def test_glyph_atlas_compacts_equivalent_grouped_numeric_reads(tmp_path):
    source_sha256 = "b" * 64
    _draw_value(tmp_path / "grouped.png", "-2846.292")
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({"records": [{
        "source_sha256": source_sha256,
        "block_id": "#/table",
        "row": 0,
        "column": 0,
        "actual": "-2 846.292",
        "reference_actual": "-2846.292",
    }]}))
    manifest_path = tmp_path / "crops.json"
    manifest_path.write_text(json.dumps({"crops": [{
        "source_sha256": source_sha256,
        "block_id": "#/table",
        "source_row": 0,
        "source_column": 0,
        "path": "grouped.png",
    }]}))

    result = glyphs.evaluate(report_path, manifest_path, source_sha256)

    assert result["reader_agreement_cells"] == 1
    assert result["atlas_cells"] == 1
    assert result["reader_disagreements"]["checked"] == 0


def test_glyph_segmentation_ignores_horizontal_table_rules(tmp_path):
    _draw_value_with_rule(tmp_path / "ruled.png", "88.3")

    glyph_images = glyphs._segment(tmp_path / "ruled.png", 4)

    assert glyph_images is not None
    assert len(glyph_images) == 4


def test_glyph_segmentation_splits_two_touching_fixed_width_digits(tmp_path):
    image = Image.new("L", (50, 30), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((5, 5, 24, 25), fill=0)
    draw.rectangle((24, 5, 43, 25), fill=0)
    image.save(tmp_path / "touching.png")

    glyph_images = glyphs._segment(tmp_path / "touching.png", 2)

    assert glyph_images is not None
    assert len(glyph_images) == 2
