"""Pixel projections locate row geometry without OCR text or token boxes."""

from __future__ import annotations

from PIL import Image, ImageDraw

from pdf2md.row_locator import (
    projection_cell_box,
    projection_column_runs,
    projection_lane_run,
    projection_panel_bounds,
    projection_row_bands,
)


def test_projection_cell_box_uses_row_scaled_padding_and_clips():
    assert projection_cell_box([20, 80], [10, 26], (100, 50)) == [16, 8, 84, 28]
    assert projection_cell_box([1, 99], [1, 49], (100, 50)) == [0, 0, 100, 50]


def test_projection_lane_run_encloses_separated_characters():
    image = Image.new("L", (100, 50), 255)
    drawing = ImageDraw.Draw(image)
    drawing.rectangle((20, 10, 25, 20), fill=0)
    drawing.rectangle((40, 10, 45, 20), fill=0)

    assert projection_lane_run(image, [8, 24], [10, 60]) == (20, 46)
    assert projection_lane_run(image, [30, 40], [10, 60]) is None


def _panel_image(rows: int, *, header: bool = True) -> Image.Image:
    image = Image.new("L", (400, 240), 255)
    drawing = ImageDraw.Draw(image)
    if header:
        drawing.rectangle((205, 10, 225, 18), fill=0)
    drawing.rectangle((200, 35, 245, 37), fill=0)
    for index in range(rows):
        top = 60 + index * 25
        drawing.rectangle((205, top, 235, top + 9), fill=0)
    for top in (70, 105, 140):
        drawing.rectangle((5, top, 80, top + 8), fill=0)
    return image


def test_projection_locator_selects_right_panel_rows_and_ignores_rules():
    bands, evidence, refusal = projection_row_bands(
        _panel_image(5), 5, panel_index=1, panel_count=2
    )

    assert bands == [(60, 70), (85, 95), (110, 120), (135, 145), (160, 170)]
    assert evidence == {
        "method": "leading_panel_stripe_horizontal_ink_projection",
        "panel_index": 1,
        "panel_count": 2,
        "stripe_bounds": [200, 250],
        "stripe_fraction": 0.25,
        "dark_threshold": 200,
        "minimum_dark_pixels": 1,
        "raw_bands": 7,
        "nonrule_bands": 6,
        "expected_rows": 5,
        "minimum_band_height": 4,
        "text_bands": 6,
        "selected_bands": 5,
        "bands": [
            [60, 70], [85, 95], [110, 120], [135, 145], [160, 170],
        ],
    }
    assert refusal is None


def test_projection_locator_refuses_too_few_rows():
    bands, evidence, refusal = projection_row_bands(
        _panel_image(2, header=False), 3, panel_index=1, panel_count=2
    )

    assert bands is None
    assert evidence["text_bands"] == 2
    assert refusal == "projection_row_count_mismatch"


def test_panel_locator_finds_unequal_panels_and_excludes_gutter():
    image = Image.new("L", (500, 240), 255)
    drawing = ImageDraw.Draw(image)
    for left, right in ((20, 125), (210, 475)):
        for top in range(40, 190, 25):
            drawing.rectangle((left, top, right, top + 9), fill=0)

    bounds, evidence, refusal = projection_panel_bounds(image, 2)

    assert bounds == [(0, 126), (210, 500)]
    assert evidence["selected_gaps"] == [[126, 210]]
    assert refusal is None


def test_panel_locator_refuses_equally_plausible_gutters():
    image = Image.new("L", (600, 240), 255)
    drawing = ImageDraw.Draw(image)
    for left, right in ((20, 130), (230, 330), (430, 580)):
        for top in range(40, 190, 25):
            drawing.rectangle((left, top, right, top + 9), fill=0)

    bounds, evidence, refusal = projection_panel_bounds(image, 2)

    assert bounds is None
    assert evidence["candidate_gaps"][:2] == [[131, 230], [331, 430]]
    assert refusal == "projection_panel_gutters_ambiguous"


def test_panel_locator_finds_three_unequal_panels():
    image = Image.new("L", (900, 240), 255)
    drawing = ImageDraw.Draw(image)
    for left, right in ((20, 180), (290, 480), (610, 880)):
        for top in range(40, 190, 25):
            drawing.rectangle((left, top, right, top + 9), fill=0)

    bounds, evidence, refusal = projection_panel_bounds(image, 3)

    assert bounds == [(0, 181), (290, 481), (610, 900)]
    assert evidence["selected_gaps"] == [[181, 290], [481, 610]]
    assert refusal is None


def test_projection_locator_uses_detected_panel_bounds():
    image = Image.new("L", (500, 240), 255)
    drawing = ImageDraw.Draw(image)
    for top in range(60, 185, 25):
        drawing.rectangle((230, top, 260, top + 9), fill=0)
    bounds = [(0, 125), (210, 500)]

    bands, evidence, refusal = projection_row_bands(
        image,
        5,
        panel_index=1,
        panel_count=2,
        stripe_fraction=0.25,
        panel_bounds=bounds,
    )

    assert bands == [(60, 70), (85, 95), (110, 120), (135, 145), (160, 170)]
    assert evidence["stripe_bounds"] == [210, 282]
    assert evidence["panel_bounds"] == [[0, 125], [210, 500]]
    assert refusal is None


def test_column_locator_merges_characters_into_structural_columns():
    image = Image.new("L", (300, 100), 255)
    drawing = ImageDraw.Draw(image)
    for top in (10, 40):
        for left in (10, 110, 210):
            drawing.rectangle((left, top, left + 10, top + 9), fill=0)
            drawing.rectangle((left + 16, top, left + 26, top + 9), fill=0)

    rows, evidence, refusal = projection_column_runs(
        image, [(10, 20), (40, 50)], (0, 300), 3
    )

    assert rows == [
        [(10, 37), (110, 137), (210, 237)],
        [(10, 37), (110, 137), (210, 237)],
    ]
    assert evidence["merge_gap"] == 10
    assert evidence["exact_rows"] == 2
    assert refusal is None


def test_column_locator_refuses_only_the_ambiguous_row():
    image = Image.new("L", (300, 100), 255)
    drawing = ImageDraw.Draw(image)
    for left in (10, 110, 210):
        drawing.rectangle((left, 10, left + 20, 19), fill=0)
        drawing.rectangle((left, 40, left + 20, 49), fill=0)
    drawing.rectangle((70, 40, 74, 49), fill=0)

    rows, evidence, refusal = projection_column_runs(
        image, [(10, 20), (40, 50)], (0, 300), 3
    )

    assert rows == [[(10, 31), (110, 131), (210, 231)], None]
    assert evidence["run_counts"] == [3, 4]
    assert evidence["refused_rows"] == 1
    assert refusal is None


def test_column_locator_refuses_when_no_row_has_the_expected_count():
    image = Image.new("L", (300, 60), 255)
    drawing = ImageDraw.Draw(image)
    for left in (10, 70, 110, 210):
        drawing.rectangle((left, 10, left + 20, 19), fill=0)

    rows, evidence, refusal = projection_column_runs(
        image, [(10, 20)], (0, 300), 3
    )

    assert rows is None
    assert evidence["run_counts"] == [4]
    assert refusal == "projection_columns_unavailable"
