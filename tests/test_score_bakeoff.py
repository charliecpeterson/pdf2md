"""Native bake-off scoring must pin exact chart structure and coordinates."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "score_bakeoff", Path(__file__).parent.parent / "scripts" / "score_bakeoff.py"
)
sb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sb)


def test_pdf2md_reader_extracts_each_csv_series(tmp_path):
    (tmp_path / "document.md").write_text(
        """```csv
# x scale: linear
# series 1
x,y
0,0
1,1

# panel 2 series 1
x,y
0,2
1,3
```
"""
    )

    assert sb.extract_charts("pdf2md-current", tmp_path) == [
        [[(0.0, 0.0), (1.0, 1.0)], [(0.0, 2.0), (1.0, 3.0)]]
    ]


def test_pdf2md_reader_prefers_external_chart_csv(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "figure.csv").write_text(
        "# x scale: linear\n# y scale: linear\n\n"
        "# series 1\nx,y\n0,1\n1,2\n"
    )
    (tmp_path / "document.md").write_text("No inline chart data.\n")

    assert sb.extract_charts("pdf2md-current", tmp_path) == [
        [[(0.0, 1.0), (1.0, 2.0)]]
    ]


def test_pdf2md_page_vlm_uses_the_same_native_readers(tmp_path):
    (tmp_path / "document.md").write_text(
        "| A | B |\n|---|---|\n| 1 | x |\n"
    )

    assert sb.extract_tables("pdf2md-page-vlm", tmp_path) == [[
        ["A", "B"],
        ["1", "x"],
    ]]


def test_docling_reader_uses_native_chart_grid(tmp_path):
    def cells(values):
        return [{"text": value} for value in values]

    document = {
        "schema_name": "DoclingDocument",
        "pictures": [{
            "meta": {"tabular_chart": {"chart_data": {"grid": [
                cells(["X", "up", "down"]),
                cells(["0", "0", "2"]),
                cells(["1", "1", "1"]),
            ]}}}
        }],
    }
    (tmp_path / "document.json").write_text(json.dumps(document))

    assert sb.extract_charts("docling-standard", tmp_path) == [
        [[(0.0, 0.0), (1.0, 1.0)], [(0.0, 2.0), (1.0, 1.0)]]
    ]
    assert sb.extract_charts("docling-egret-large", tmp_path) == [
        [[(0.0, 0.0), (1.0, 1.0)], [(0.0, 2.0), (1.0, 1.0)]]
    ]


def test_chart_score_matches_series_independent_of_order():
    truth = [{
        "x_tolerance": 0.01,
        "y_tolerance": 0.05,
        "series": [
            [[0, 0], [1, 1]],
            [[0, 2], [1, 1]],
        ],
    }]
    candidate = [[
        [(0.0, 2.02), (1.0, 1.02)],
        [(0.0, 0.01), (1.0, 1.01)],
    ]]

    assert all(ok for ok, _ in sb.score_charts(candidate, truth))


def test_chart_score_pins_count_points_and_error():
    truth = [{
        "tolerance": 0.05,
        "series": [[[0, 0], [1, 1], [2, 4]]],
    }]
    wrong = [[[(0.0, 0.0), (1.0, 9.0)]]]

    facts = sb.score_charts(wrong, truth)
    assert facts == [
        (True, "chart count == 1 (got 1)"),
        (True, "chart 1 series count == 1 (got 1)"),
        (False, "chart 1 max x error <= 0.05 (got inf)"),
        (False, "chart 1 max y error <= 0.05 (got inf)"),
    ]


def test_missing_chart_fails_the_same_four_facts():
    truth = [{
        "x_tolerance": 0.05,
        "y_tolerance": 0.1,
        "series": [[[0, 0], [1, 1]]],
    }]

    assert sb.score_charts([], truth) == [
        (False, "chart count == 1 (got 0)"),
        (False, "chart 1 series count == 1 (missing)"),
        (False, "chart 1 max x error <= 0.05 (missing)"),
        (False, "chart 1 max y error <= 0.1 (missing)"),
    ]


def test_latest_run_does_not_hide_new_failure(tmp_path):
    engine_dir = tmp_path / "vector-plot" / "docling-standard"
    old = engine_dir / "20260101T000000Z"
    new = engine_dir / "20260102T000000Z"
    old.mkdir(parents=True)
    new.mkdir()
    (old / "run.json").write_text(json.dumps({"status": "ok"}))
    (new / "run.json").write_text(json.dumps({"status": "failed"}))

    run_dir, run = sb._latest_run(tmp_path, "vector-plot", "docling-standard")

    assert run_dir == new
    assert run["status"] == "failed"


def test_pdf2md_reader_extracts_markdown_table(tmp_path):
    (tmp_path / "document.md").write_text(
        """Before

| Extension | Type of file |
|---|---|
| c | List of CSFs. |
| uni.lsj.lbl | Wrapped description joined into one cell. |

After
"""
    )

    assert sb.extract_tables("pdf2md-current", tmp_path) == [[
        ["Extension", "Type of file"],
        ["c", "List of CSFs."],
        ["uni.lsj.lbl", "Wrapped description joined into one cell."],
    ]]


def test_docling_reader_extracts_native_table_grid(tmp_path):
    document = {
        "schema_name": "DoclingDocument",
        "tables": [{"data": {"grid": [
            [{"text": "Extension"}, {"text": "Type of file"}],
            [{"text": "c"}, {"text": "List of CSFs."}],
        ]}}],
    }
    (tmp_path / "document.json").write_text(json.dumps(document))

    assert sb.extract_tables("docling-standard", tmp_path) == [[
        ["Extension", "Type of file"],
        ["c", "List of CSFs."],
    ]]


def test_html_table_readers_use_native_paddle_and_mineru_json(tmp_path):
    html = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>x</td></tr></table>"
    paddle = tmp_path / "paddle"
    paddle.mkdir()
    (paddle / "page_res.json").write_text(json.dumps({
        "parsing_res_list": [{"block_label": "table", "block_content": html}],
    }))
    mineru = tmp_path / "mineru"
    mineru.mkdir()
    (mineru / "page_content_list.json").write_text(json.dumps([
        {"type": "table", "table_body": html},
    ]))

    expected = [[['A', 'B'], ['1', 'x']]]
    assert sb.extract_tables("paddleocr-vl", paddle) == expected
    assert sb.extract_tables("mineru", mineru) == expected


def test_table_score_pins_shape_order_and_every_cell():
    expected = [{"rows": [
        ["Extension", "Type of file"],
        ["c", "List of CSFs."],
        ["w", "Binary file of radial wave functions."],
    ]}]
    candidate = [[
        ["Extension", "Type of file"],
        ["c", "List of CSFs."],
        ["w", "wrong"],
    ]]

    facts = sb.score_tables(candidate, expected)

    assert len(facts) == 6
    assert [ok for ok, _ in facts] == [True, True, True, True, True, False]
    assert "Binary file of radial wave functions" in facts[-1][1]


def test_table_score_normalizes_inline_math_without_hiding_plain_spacing():
    labels = [{"rows": [["h", "Landé gJ-factors"]]}]

    assert all(ok for ok, _ in sb.score_tables(
        [[['h', r'Landé $g_J$ -factors']]], labels
    ))
    facts = sb.score_tables([[['h', 'Landé gJ -factors']]], labels)
    assert facts[-1][0] is False


def test_reading_order_requires_presence_and_sequence():
    snippets = ["Before", "Table 3.1", "Extension", "After"]

    assert all(ok for ok, _ in sb.score_reading_order(
        "Before\nTable 3.1\nExtension\nAfter", snippets
    ))
    facts = sb.score_reading_order("Before\nExtension\nAfter\nTable 3.1", snippets)
    assert [ok for ok, _ in facts] == [True, True, True, True, False]

    missing = sb.score_reading_order("Before\nAfter", snippets)
    assert [ok for ok, _ in missing] == [True, False, False, True, False]


def test_reading_order_ignores_capitalization():
    assert all(ok for ok, _ in sb.score_reading_order(
        "Before\nFIG. 1-2.\nAfter", ["Before", "Fig. 1-2.", "After"]
    ))


def test_reading_order_uses_whole_words_and_an_ordered_subsequence():
    snippets = ["Table of common extensions.", "Extension", "After"]

    assert all(ok for ok, _ in sb.score_reading_order(
        "Extension\nTable of common extensions.\nExtension\nAfter", snippets
    ))
    facts = sb.score_reading_order(
        "Extension\nTable of common extensions.\nAfter", snippets
    )
    assert [ok for ok, _ in facts] == [True, True, True, False]


def test_figure_score_pins_page_containment_asset_and_text():
    labels = [{
        "page": 1,
        "page_size": [100, 200],
        "min_page_area_fraction": 0.8,
        "forbid_unverified_structured_data": True,
        "required_text": ["Primary-vortex center", "Table 5"],
    }]
    candidate = [{
        "page": 1,
        "bbox": {"x0": 0, "y0": 200, "x1": 100, "y1": 20},
        "asset_exists": True,
        "text": "PRIMARY VORTEX CENTER; listed in Table5",
        "structured_data": False,
    }]

    assert all(ok for ok, _ in sb.score_figures(candidate, labels, ""))

    clipped = [{
        "page": 2,
        "bbox": {"l": 0, "t": 0, "r": 50, "b": 100},
        "asset_exists": False,
        "text": "",
        "structured_data": True,
    }]
    assert [ok for ok, _ in sb.score_figures(clipped, labels, "")] == [
        True, False, False, False, False, False, False
    ]


def test_pdf2md_equation_reader_uses_provenance_and_crop(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "equation.png").write_bytes(b"png")
    (tmp_path / "provenance.json").write_text(json.dumps({
        "blocks": [{
            "type": "equation",
            "page": 1,
            "text": r"E = mc^2 \tag{2.1}",
            "extra": {"crop_path": "assets/equation.png"},
        }],
    }))

    assert sb.extract_equations("pdf2md-current", tmp_path) == [{
        "page": 1,
        "label": "2.1",
        "latex": r"E = mc^2 \tag{2.1}",
        "asset_exists": True,
    }]


def test_docling_equation_reader_uses_native_formula(tmp_path):
    (tmp_path / "document.json").write_text(json.dumps({
        "schema_name": "DoclingDocument",
        "texts": [{
            "label": "formula",
            "text": r"E = mc^2",
            "prov": [{"page_no": 1}],
        }],
    }))

    assert sb.extract_equations("docling-standard", tmp_path) == [{
        "page": 1,
        "label": None,
        "latex": r"E = mc^2",
        "asset_exists": False,
    }]


def test_paddleocr_readers_use_native_blocks_and_saved_chart(tmp_path):
    image_dir = tmp_path / "imgs"
    image_dir.mkdir()
    (image_dir / "img_in_chart_box_10_20_90_180.jpg").write_bytes(b"jpg")
    (tmp_path / "page_0_res.json").write_text(json.dumps({
        "page_index": 0,
        "width": 100,
        "height": 200,
        "parsing_res_list": [
            {"block_label": "chart", "block_content": "", "block_bbox": [10, 20, 90, 180]},
            {"block_label": "figure_title", "block_content": "Primary vortex"},
            {"block_label": "display_formula", "block_content": r"$$ E = mc^2 $$"},
            {"block_label": "formula_number", "block_content": "(2.1)"},
        ],
    }))

    assert sb.extract_figures("paddleocr-vl", tmp_path) == [{
        "page": 1,
        "bbox": {"x0": 10, "y0": 20, "x1": 90, "y1": 180},
        "page_size": [100, 200],
        "asset_exists": True,
        "text": "\nPrimary vortex\n$$ E = mc^2 $$\n(2.1)",
        "structured_data": False,
    }]
    assert sb.extract_equations("paddleocr-vl", tmp_path) == [{
        "page": 1,
        "label": "2.1",
        "latex": r"$$ E = mc^2 $$",
        "asset_exists": False,
    }]


def test_mineru_readers_use_middle_json_and_source_crops(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    (images / "chart.jpg").write_bytes(b"chart")
    (images / "equation.jpg").write_bytes(b"equation")
    (tmp_path / "page_middle.json").write_text(json.dumps({
        "pdf_info": [{
            "page_idx": 0,
            "page_size": [100, 200],
            "preproc_blocks": [
                {
                    "type": "chart",
                    "bbox": [10, 20, 90, 180],
                    "blocks": [{"lines": [{"spans": [{
                        "type": "chart",
                        "content": "invented table",
                        "image_path": "chart.jpg",
                    }]}]}],
                },
                {
                    "type": "interline_equation",
                    "bbox": [20, 150, 80, 170],
                    "lines": [{"spans": [{
                        "type": "interline_equation",
                        "content": r"E = mc^2 \tag{2.1}",
                        "image_path": "equation.jpg",
                    }]}],
                },
            ],
        }],
    }))

    assert sb.extract_figures("mineru", tmp_path) == [{
        "page": 1,
        "bbox": {"x0": 10, "y0": 20, "x1": 90, "y1": 180},
        "page_size": [100, 200],
        "asset_exists": True,
        "text": "invented table",
        "structured_data": True,
    }]
    assert sb.extract_equations("mineru", tmp_path) == [{
        "page": 1,
        "label": "2.1",
        "latex": r"E = mc^2 \tag{2.1}",
        "asset_exists": True,
    }]


def test_equation_score_requires_label_exact_latex_and_crop():
    labels = [{
        "page": 1,
        "label": "4.1",
        "latex": r"H_T = \boldsymbol{\alpha}_i",
    }]
    candidate = [{
        "page": 1,
        "label": "4.1",
        "latex": r"H _ { T } = \mathbf { \alpha } _ { i }",
        "asset_exists": True,
    }]

    assert all(ok for ok, _ in sb.score_equations(candidate, labels))

    lossy = [{
        "page": 1,
        "label": None,
        "latex": r"H_T = \alpha_i",
        "asset_exists": False,
    }]
    assert [ok for ok, _ in sb.score_equations(lossy, labels)] == [
        True, True, False, False, False
    ]


def test_equation_score_ignores_presentational_alignment_environment():
    labels = [{
        "page": 1,
        "label": "1-27",
        "latex": r"E = \frac{1}{2}mv^2 = \frac{p^2}{2m}",
    }]
    candidate = [{
        "page": 1,
        "label": "1-27",
        "latex": (
            r"\begin{array}{l} E &= \frac{1}{2}m v^2 \\ "
            r"&= \frac{p^2}{2m} \end{array}"
        ),
        "asset_exists": True,
    }]

    assert all(ok for ok, _ in sb.score_equations(candidate, labels))
