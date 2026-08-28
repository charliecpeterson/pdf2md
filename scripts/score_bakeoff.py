"""Score native bake-off output against pinned source-derived facts.

    uv run python scripts/score_bakeoff.py out/bakeoff \
        --engine docling-standard --document vector-plot --strict

Readers are intentionally engine-specific. They recover only the native fields
needed by a labelled check and do not create a shared conversion schema.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any

from pdf2md.tables import html_tables


_LABELS = Path(__file__).parent.parent / "tests" / "bakeoff_labels.json"
_ENGINE_PINS = Path(__file__).parent.parent / "tests" / "bakeoff_engine_pins.json"
_ROOT = Path(__file__).parent.parent
_CSV_BLOCK = re.compile(r"```csv\s*\n(.*?)```", re.DOTALL)
_SERIES_HEADER = re.compile(r"# (?:panel \d+ )?series \d+$")
_TABLE_SEPARATOR_CELL = re.compile(r":?-{3,}:?")
_EQUATION_TAG = re.compile(r"\\tag\s*\{([^}]+)\}")

Point = tuple[float, float]
Series = list[Point]
Chart = list[Series]
Table = list[list[str]]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_pdf2md(engine_id: str) -> bool:
    return engine_id.startswith("pdf2md-")


def _is_docling(engine_id: str) -> bool:
    return engine_id.startswith("docling-")


def _number(value: Any) -> float | None:
    try:
        return float(str(value).strip().replace("−", "-").replace(",", ""))
    except (TypeError, ValueError):
        return None


def _docling_charts(native_dir: Path) -> list[Chart]:
    charts: list[Chart] = []
    for path in sorted(native_dir.rglob("*.json")):
        document = json.loads(path.read_text())
        if document.get("schema_name") != "DoclingDocument":
            continue
        for picture in document.get("pictures", []):
            meta = picture.get("meta") or {}
            tabular = meta.get("tabular_chart") or {}
            grid = (tabular.get("chart_data") or {}).get("grid") or []
            if len(grid) < 2 or len(grid[0]) < 2:
                continue
            series: Chart = [[] for _ in grid[0][1:]]
            for row in grid[1:]:
                if len(row) != len(grid[0]):
                    continue
                x = _number(row[0].get("text"))
                ys = [_number(cell.get("text")) for cell in row[1:]]
                if x is None or any(y is None for y in ys):
                    continue
                for output, y in zip(series, ys):
                    output.append((x, y))
            if series and all(series):
                charts.append(series)
    return charts


def _pdf2md_charts(native_dir: Path) -> list[Chart]:
    charts: list[Chart] = []
    data_files = sorted(native_dir.rglob("data/*.csv"))
    if data_files:
        for path in data_files:
            chart = _pdf2md_chart_csv(path.read_text())
            if chart:
                charts.append(chart)
        return charts
    for path in sorted(native_dir.rglob("*.md")):
        for match in _CSV_BLOCK.finditer(path.read_text()):
            chart = _pdf2md_chart_csv(match.group(1))
            if chart:
                charts.append(chart)
    return charts


def _pdf2md_chart_csv(text: str) -> Chart:
    chart: Chart = []
    series: Series = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _SERIES_HEADER.fullmatch(line):
            if series:
                chart.append(series)
                series = []
            continue
        if line.startswith("#"):
            continue
        row = next(csv.reader([line]))
        if len(row) != 2:
            continue
        x, y = _number(row[0]), _number(row[1])
        if x is not None and y is not None:
            series.append((x, y))
    if series:
        chart.append(series)
    return chart


def extract_charts(engine_id: str, native_dir: Path) -> list[Chart]:
    if _is_pdf2md(engine_id):
        return _pdf2md_charts(native_dir)
    if _is_docling(engine_id):
        return _docling_charts(native_dir)
    raise ValueError(f"no native chart reader yet for {engine_id}")


def _markdown_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _pdf2md_tables(native_dir: Path) -> list[Table]:
    tables: list[Table] = []
    for path in sorted(native_dir.rglob("*.md")):
        lines = path.read_text().splitlines()
        index = 0
        while index + 1 < len(lines):
            header = _markdown_row(lines[index]) if "|" in lines[index] else []
            separator = _markdown_row(lines[index + 1]) if "|" in lines[index + 1] else []
            if (
                header
                and len(separator) == len(header)
                and all(_TABLE_SEPARATOR_CELL.fullmatch(cell) for cell in separator)
            ):
                rows = [header]
                index += 2
                while index < len(lines) and lines[index].strip().startswith("|"):
                    row = _markdown_row(lines[index])
                    if len(row) != len(header):
                        break
                    rows.append(row)
                    index += 1
                tables.append(rows)
                continue
            index += 1
    return tables


def _docling_tables(native_dir: Path) -> list[Table]:
    tables: list[Table] = []
    for path in sorted(native_dir.rglob("*.json")):
        document = json.loads(path.read_text())
        if document.get("schema_name") != "DoclingDocument":
            continue
        for table in document.get("tables", []):
            grid = (table.get("data") or {}).get("grid") or []
            rows = [[str(cell.get("text", "")).strip() for cell in row] for row in grid]
            if rows:
                tables.append(rows)
    return tables


def _paddleocr_tables(native_dir: Path) -> list[Table]:
    tables: list[Table] = []
    for path in sorted(native_dir.rglob("*_res.json")):
        page = json.loads(path.read_text())
        for block in page.get("parsing_res_list", []):
            if block.get("block_label") == "table":
                tables.extend(html_tables(block.get("block_content", "")))
    return tables


def _mineru_tables(native_dir: Path) -> list[Table]:
    tables: list[Table] = []
    for path in sorted(native_dir.rglob("*_content_list.json")):
        content = json.loads(path.read_text())
        for block in content:
            if block.get("type") == "table":
                tables.extend(html_tables(block.get("table_body", "")))
    return tables


def extract_tables(engine_id: str, native_dir: Path) -> list[Table]:
    if _is_pdf2md(engine_id):
        return _pdf2md_tables(native_dir)
    if _is_docling(engine_id):
        return _docling_tables(native_dir)
    if engine_id == "paddleocr-vl":
        return _paddleocr_tables(native_dir)
    if engine_id == "mineru":
        return _mineru_tables(native_dir)
    raise ValueError(f"no native table reader yet for {engine_id}")


def extract_markdown(engine_id: str, native_dir: Path) -> str:
    paths = sorted(native_dir.rglob("*.md"))
    if _is_pdf2md(engine_id):
        paths = [path for path in paths if path.name != "README.md"]
    return "\n".join(path.read_text() for path in paths)


def _bbox_area(bbox: dict[str, Any]) -> float:
    if "x0" in bbox:
        return abs(float(bbox["x1"]) - float(bbox["x0"])) * abs(
            float(bbox["y1"]) - float(bbox["y0"])
        )
    return abs(float(bbox["r"]) - float(bbox["l"])) * abs(
        float(bbox["b"]) - float(bbox["t"])
    )


def _pdf2md_figures(native_dir: Path) -> list[dict[str, Any]]:
    figures: list[dict[str, Any]] = []
    for path in sorted(native_dir.rglob("provenance.json")):
        provenance = json.loads(path.read_text())
        for figure in provenance.get("figures", []):
            labels = figure.get("labels") or {}
            asset = path.parent / figure.get("asset_path", "")
            figures.append(
                {
                    "page": figure.get("page"),
                    "bbox": figure.get("bbox") or {},
                    "asset_exists": asset.is_file(),
                    "text": "\n".join(
                        filter(None, [figure.get("caption"), labels.get("text")])
                    ),
                }
            )
    return figures


def _docling_figures(native_dir: Path) -> list[dict[str, Any]]:
    figures: list[dict[str, Any]] = []
    for path in sorted(native_dir.rglob("*.json")):
        document = json.loads(path.read_text())
        if document.get("schema_name") != "DoclingDocument":
            continue
        for picture in document.get("pictures", []):
            provenance = picture.get("prov") or [{}]
            image = picture.get("image") or {}
            asset = Path(image.get("uri", ""))
            figures.append(
                {
                    "page": provenance[0].get("page_no"),
                    "bbox": provenance[0].get("bbox") or {},
                    "asset_exists": asset.is_file(),
                    "text": "",
                }
            )
    return figures


def _paddleocr_figures(native_dir: Path) -> list[dict[str, Any]]:
    figures: list[dict[str, Any]] = []
    for path in sorted(native_dir.rglob("*_res.json")):
        page = json.loads(path.read_text())
        page_text = "\n".join(
            block.get("block_content", "") for block in page.get("parsing_res_list", [])
        )
        for block in page.get("parsing_res_list", []):
            if block.get("block_label") not in {"chart", "figure", "image"}:
                continue
            x0, y0, x1, y1 = block["block_bbox"]
            coordinates = "_".join(str(value) for value in block["block_bbox"])
            asset = path.parent / "imgs" / (
                f"img_in_{block['block_label']}_box_{coordinates}.jpg"
            )
            figures.append(
                {
                    "page": int(page.get("page_index", 0)) + 1,
                    "bbox": {"x0": x0, "y0": y0, "x1": x1, "y1": y1},
                    "page_size": [page.get("width"), page.get("height")],
                    "asset_exists": asset.is_file(),
                    "text": page_text,
                    "structured_data": bool(block.get("block_content")),
                }
            )
    return figures


def _nested_values(value: Any, key: str) -> list[Any]:
    if isinstance(value, dict):
        matches = [value[key]] if key in value else []
        return matches + sum((_nested_values(item, key) for item in value.values()), [])
    if isinstance(value, list):
        return sum((_nested_values(item, key) for item in value), [])
    return []


def _mineru_figures(native_dir: Path) -> list[dict[str, Any]]:
    figures: list[dict[str, Any]] = []
    for path in sorted(native_dir.rglob("*_middle.json")):
        document = json.loads(path.read_text())
        for page in document.get("pdf_info", []):
            for block in page.get("preproc_blocks", []):
                if block.get("type") not in {"chart", "image"}:
                    continue
                x0, y0, x1, y1 = block["bbox"]
                image_paths = _nested_values(block, "image_path")
                asset = path.parent / "images" / image_paths[0] if image_paths else None
                contents = _nested_values(block, "content")
                figures.append(
                    {
                        "page": int(page.get("page_idx", 0)) + 1,
                        "bbox": {"x0": x0, "y0": y0, "x1": x1, "y1": y1},
                        "page_size": page.get("page_size"),
                        "asset_exists": asset is not None and asset.is_file(),
                        "text": "\n".join(str(content) for content in contents),
                        "structured_data": any(
                            span_type == "chart" for span_type in _nested_values(block, "type")
                        ),
                    }
                )
    return figures


def extract_figures(engine_id: str, native_dir: Path) -> list[dict[str, Any]]:
    if _is_pdf2md(engine_id):
        return _pdf2md_figures(native_dir)
    if _is_docling(engine_id):
        return _docling_figures(native_dir)
    if engine_id == "paddleocr-vl":
        return _paddleocr_figures(native_dir)
    if engine_id == "mineru":
        return _mineru_figures(native_dir)
    raise ValueError(f"no native figure reader yet for {engine_id}")


def _equation_label(latex: str) -> str | None:
    match = _EQUATION_TAG.search(latex)
    return match.group(1).strip() if match else None


def _pdf2md_equations(native_dir: Path) -> list[dict[str, Any]]:
    equations: list[dict[str, Any]] = []
    for path in sorted(native_dir.rglob("provenance.json")):
        provenance = json.loads(path.read_text())
        for block in provenance.get("blocks", []):
            if block.get("type") != "equation":
                continue
            latex = block.get("text", "")
            crop_path = (block.get("extra") or {}).get("crop_path")
            crop = path.parent / crop_path if crop_path else None
            equations.append(
                {
                    "page": block.get("page"),
                    "label": _equation_label(latex),
                    "latex": latex,
                    "asset_exists": crop is not None and crop.is_file(),
                }
            )
    return equations


def _docling_equations(native_dir: Path) -> list[dict[str, Any]]:
    equations: list[dict[str, Any]] = []
    for path in sorted(native_dir.rglob("*.json")):
        document = json.loads(path.read_text())
        if document.get("schema_name") != "DoclingDocument":
            continue
        for text_item in document.get("texts", []):
            if text_item.get("label") != "formula":
                continue
            latex = text_item.get("text", "")
            provenance = text_item.get("prov") or [{}]
            equations.append(
                {
                    "page": provenance[0].get("page_no"),
                    "label": _equation_label(latex),
                    "latex": latex,
                    "asset_exists": False,
                }
            )
    return equations


def _paddleocr_equations(native_dir: Path) -> list[dict[str, Any]]:
    equations: list[dict[str, Any]] = []
    for path in sorted(native_dir.rglob("*_res.json")):
        page = json.loads(path.read_text())
        blocks = page.get("parsing_res_list", [])
        numbers = [
            block.get("block_content", "").strip().strip("()")
            for block in blocks
            if block.get("block_label") == "formula_number"
        ]
        formulas = [
            block.get("block_content", "")
            for block in blocks
            if block.get("block_label") == "display_formula"
        ]
        for index, latex in enumerate(formulas):
            equations.append(
                {
                    "page": int(page.get("page_index", 0)) + 1,
                    "label": numbers[index] if index < len(numbers) else _equation_label(latex),
                    "latex": latex,
                    "asset_exists": False,
                }
            )
    return equations


def _mineru_equations(native_dir: Path) -> list[dict[str, Any]]:
    equations: list[dict[str, Any]] = []
    for path in sorted(native_dir.rglob("*_middle.json")):
        document = json.loads(path.read_text())
        for page in document.get("pdf_info", []):
            for block in page.get("preproc_blocks", []):
                if block.get("type") != "interline_equation":
                    continue
                latex_values = _nested_values(block, "content")
                latex = str(latex_values[0]) if latex_values else ""
                image_paths = _nested_values(block, "image_path")
                crop = path.parent / "images" / image_paths[0] if image_paths else None
                equations.append(
                    {
                        "page": int(page.get("page_idx", 0)) + 1,
                        "label": _equation_label(latex),
                        "latex": latex,
                        "asset_exists": crop is not None and crop.is_file(),
                    }
                )
    return equations


def extract_equations(engine_id: str, native_dir: Path) -> list[dict[str, Any]]:
    if _is_pdf2md(engine_id):
        return _pdf2md_equations(native_dir)
    if _is_docling(engine_id):
        return _docling_equations(native_dir)
    if engine_id == "paddleocr-vl":
        return _paddleocr_equations(native_dir)
    if engine_id == "mineru":
        return _mineru_equations(native_dir)
    raise ValueError(f"no native equation reader yet for {engine_id}")


def _normalize_latex(latex: str) -> str:
    normalized = latex.strip().strip("$")
    normalized = _EQUATION_TAG.sub("", normalized)
    normalized = re.sub(
        r"\\(?:begin|end)\s*\{(?:aligned|array|gathered|split)\}"
        r"(?:\s*\{[^}]*\})?",
        "",
        normalized,
    )
    normalized = normalized.replace("&", "")
    normalized = normalized.replace(r"\left.", "").replace(r"\right.", "")
    normalized = re.sub(r"\\(?:left|right|quad|qquad)", "", normalized)
    normalized = re.sub(r"\\[,;:!]", "", normalized)
    normalized = normalized.replace(r"\\", "")
    normalized = re.sub(r"\\(?:mathbf|bm)(?=\s*\{)", r"\\boldsymbol", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    normalized = re.sub(r"([_^])([A-Za-z0-9])", r"\1{\2}", normalized)
    return normalized.rstrip(",.;")


def _series_error(candidate: Series, truth: Series) -> tuple[float, float]:
    if len(candidate) != len(truth):
        return math.inf, math.inf
    candidate = sorted(candidate)
    truth = sorted(truth)
    return (
        max(abs(got[0] - want[0]) for got, want in zip(candidate, truth)),
        max(abs(got[1] - want[1]) for got, want in zip(candidate, truth)),
    )


def _chart_error(candidate: Chart, truth: Chart) -> tuple[float, float]:
    if len(candidate) != len(truth) or len(truth) > 7:
        return math.inf, math.inf
    best = (math.inf, math.inf)
    for ordering in itertools.permutations(candidate):
        errors = [_series_error(got, want) for got, want in zip(ordering, truth)]
        score = (max(error[0] for error in errors), max(error[1] for error in errors))
        if score < best:
            best = score
    return best


def score_charts(candidate: list[Chart], labels: list[dict[str, Any]]) -> list[tuple[bool, str]]:
    facts: list[tuple[bool, str]] = [
        (len(candidate) == len(labels), f"chart count == {len(labels)} (got {len(candidate)})")
    ]
    for index, label in enumerate(labels):
        if index >= len(candidate):
            x_tolerance = float(label.get("x_tolerance", label.get("tolerance", 0)))
            y_tolerance = float(label.get("y_tolerance", label.get("tolerance", 0)))
            facts.extend([
                (False, f"chart {index + 1} series count == {len(label['series'])} (missing)"),
                (False, f"chart {index + 1} max x error <= {x_tolerance:g} (missing)"),
                (False, f"chart {index + 1} max y error <= {y_tolerance:g} (missing)"),
            ])
            continue
        truth = [
            [(float(point[0]), float(point[1])) for point in series]
            for series in label["series"]
        ]
        x_error, y_error = _chart_error(candidate[index], truth)
        x_tolerance = float(label.get("x_tolerance", label.get("tolerance", 0)))
        y_tolerance = float(label.get("y_tolerance", label.get("tolerance", 0)))
        facts.extend([
            (
                len(candidate[index]) == len(truth),
                f"chart {index + 1} series count == {len(truth)} "
                f"(got {len(candidate[index])})",
            ),
            (
                x_error <= x_tolerance,
                f"chart {index + 1} max x error <= {x_tolerance:g} (got {x_error:g})",
            ),
            (
                y_error <= y_tolerance,
                f"chart {index + 1} max y error <= {y_tolerance:g} (got {y_error:g})",
            ),
        ])
    return facts


def _clean_cell(value: str) -> str:
    cleaned = " ".join(value.split())
    inline_gj = re.compile(r"\$\s*g_\{?J\}?\s*\$")
    if inline_gj.search(cleaned):
        cleaned = inline_gj.sub("gJ", cleaned).replace("gJ -factors", "gJ-factors")
    return cleaned


def score_tables(candidate: list[Table], labels: list[dict[str, Any]]) -> list[tuple[bool, str]]:
    facts: list[tuple[bool, str]] = [
        (len(candidate) == len(labels), f"table count == {len(labels)} (got {len(candidate)})")
    ]
    for table_index, label in enumerate(labels):
        expected = [[_clean_cell(cell) for cell in row] for row in label["rows"]]
        got = (
            [[_clean_cell(cell) for cell in row] for row in candidate[table_index]]
            if table_index < len(candidate)
            else []
        )
        expected_cols = len(expected[0]) if expected else 0
        got_cols = max((len(row) for row in got), default=0)
        facts.extend([
            (
                len(got) == len(expected),
                f"table {table_index + 1} row count == {len(expected)} (got {len(got)})",
            ),
            (
                got_cols == expected_cols,
                f"table {table_index + 1} column count == {expected_cols} (got {got_cols})",
            ),
        ])
        for row_index, expected_row in enumerate(expected):
            got_row = got[row_index] if row_index < len(got) else None
            facts.append((
                got_row == expected_row,
                f"table {table_index + 1} row {row_index + 1} == {expected_row!r} "
                f"(got {got_row!r})",
            ))
    return facts


def score_reading_order(text: str, snippets: list[str]) -> list[tuple[bool, str]]:
    folded_text = text.casefold()
    patterns = []
    for snippet in snippets:
        escaped = re.escape(snippet.casefold())
        prefix = r"(?<!\w)" if snippet and snippet[0].isalnum() else ""
        suffix = r"(?!\w)" if snippet and snippet[-1].isalnum() else ""
        patterns.append(re.compile(prefix + escaped + suffix))
    matches = [pattern.search(folded_text) for pattern in patterns]
    facts = [
        (match is not None, f"reading-order text contains {snippet!r}")
        for snippet, match in zip(snippets, matches)
    ]
    cursor = 0
    in_order = True
    for pattern in patterns:
        match = pattern.search(folded_text, cursor)
        if match is None:
            in_order = False
            break
        cursor = match.end()
    facts.append((in_order, f"{len(snippets)} snippets appear in source order"))
    return facts


def _searchable(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def score_figures(
    candidate: list[dict[str, Any]], labels: list[dict[str, Any]], markdown: str
) -> list[tuple[bool, str]]:
    facts: list[tuple[bool, str]] = [
        (len(candidate) == len(labels), f"figure count == {len(labels)} (got {len(candidate)})")
    ]
    markdown_text = _searchable(markdown)
    for index, label in enumerate(labels):
        figure = candidate[index] if index < len(candidate) else {}
        page = figure.get("page")
        facts.append(
            (
                page == label["page"],
                f"figure {index + 1} page == {label['page']} (got {page})",
            )
        )
        width, height = figure.get("page_size") or label["page_size"]
        area_fraction = _bbox_area(figure.get("bbox", {})) / (width * height) if figure else 0.0
        minimum = float(label["min_page_area_fraction"])
        facts.append((
            area_fraction >= minimum,
            f"figure {index + 1} page area >= {minimum:g} (got {area_fraction:.3f})",
        ))
        asset_exists = bool(figure.get("asset_exists"))
        facts.append((asset_exists, f"figure {index + 1} has a saved image asset"))
        if label.get("forbid_unverified_structured_data"):
            structured_data = bool(figure.get("structured_data"))
            facts.append((
                not structured_data,
                f"figure {index + 1} emits no unverified structured data",
            ))
        figure_text = _searchable(figure.get("text", "")) + markdown_text
        for required in label.get("required_text", []):
            facts.append((
                _searchable(required) in figure_text,
                f"figure {index + 1} text contains {required!r}",
            ))
    return facts


def score_equations(
    candidate: list[dict[str, Any]], labels: list[dict[str, Any]]
) -> list[tuple[bool, str]]:
    facts: list[tuple[bool, str]] = [
        (
            len(candidate) == len(labels),
            f"equation count == {len(labels)} (got {len(candidate)})",
        )
    ]
    for index, label in enumerate(labels):
        equation = candidate[index] if index < len(candidate) else {}
        page = equation.get("page")
        facts.append(
            (
                page == label["page"],
                f"equation {index + 1} page == {label['page']} (got {page})",
            )
        )
        equation_label = equation.get("label")
        facts.append(
            (
                equation_label == label["label"],
                f"equation {index + 1} label == {label['label']!r} "
                f"(got {equation_label!r})",
            )
        )
        exact = _normalize_latex(equation.get("latex", "")) == _normalize_latex(
            label["latex"]
        )
        facts.append((exact, f"equation {index + 1} normalized LaTeX is exact"))
        asset_exists = bool(equation.get("asset_exists"))
        facts.append((asset_exists, f"equation {index + 1} has a saved source crop"))
    return facts


def _latest_run(output_root: Path, document_id: str, engine_id: str) -> tuple[Path, dict]:
    run_files = sorted((output_root / document_id / engine_id).glob("*/run.json"), reverse=True)
    if not run_files:
        raise FileNotFoundError(f"no run for {document_id}/{engine_id}")
    path = run_files[0]
    return path.parent, json.loads(path.read_text())


def _portable_candidate_provenance(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / "native" / "candidate.json"
    if not path.is_file():
        return None
    provenance = json.loads(path.read_text())
    artifact = dict(provenance.get("model_artifact") or {})
    artifact.pop("snapshot_path", None)
    return {
        "candidate": provenance.get("candidate"),
        "dependencies": provenance.get("dependencies"),
        "configuration": provenance.get("configuration"),
        "model_artifact": artifact,
    }


def _declared_engine_pin(engine_id: str) -> dict[str, Any] | None:
    if not _ENGINE_PINS.is_file():
        return None
    registry = json.loads(_ENGINE_PINS.read_text())
    if registry.get("schema_version") != 1:
        raise ValueError(f"{_ENGINE_PINS}: unsupported schema_version")
    engines = registry.get("engines")
    if not isinstance(engines, dict):
        raise ValueError(f"{_ENGINE_PINS}: engines must be an object")
    return engines.get(engine_id)


def _portable_version_probe(run: dict[str, Any]) -> dict[str, Any] | None:
    probe = run.get("engine", {}).get("version_probe")
    if not isinstance(probe, dict):
        return None
    return {key: value for key, value in probe.items() if key != "command"}


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(_ROOT.resolve()))
    except ValueError:
        return str(resolved)


def main() -> None:
    parser = argparse.ArgumentParser(description="Score labelled native bake-off outputs.")
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--labels", type=Path, default=_LABELS)
    parser.add_argument("--engine", action="append", required=True)
    parser.add_argument("--document", action="append", required=True)
    parser.add_argument("--json", dest="json_path", type=Path)
    parser.add_argument("--strict", action="store_true", help="Exit nonzero on any failure.")
    parser.add_argument("--check", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    label_file = json.loads(args.labels.read_text())
    if label_file.get("schema_version") != 1:
        parser.error("unsupported label schema_version")
    labels = {label["id"]: label for label in label_file.get("documents", [])}
    unknown = sorted(set(args.document) - labels.keys())
    if unknown:
        parser.error(f"unlabelled document id(s): {', '.join(unknown)}")

    failures = 0
    scored_facts = 0
    results: list[dict[str, Any]] = []
    for document_id in args.document:
        label = labels[document_id]
        for engine_id in args.engine:
            try:
                run_dir, run = _latest_run(args.output_root, document_id, engine_id)
                if run.get("status") != "ok":
                    raise ValueError(f"latest run status is {run.get('status')}")
                if run.get("source", {}).get("sha256") != label["source_sha256"]:
                    raise ValueError("source hash does not match labels")
                expected_input_hash = label.get("input_sha256")
                actual_input_hash = run.get("input", {}).get("sha256")
                if expected_input_hash and actual_input_hash != expected_input_hash:
                    raise ValueError("sampled input hash does not match labels")
                facts: list[tuple[bool, str]] = []
                if "charts" in label:
                    facts.extend(score_charts(
                        extract_charts(engine_id, run_dir / "native"), label["charts"]
                    ))
                if "tables" in label:
                    facts.extend(score_tables(
                        extract_tables(engine_id, run_dir / "native"), label["tables"]
                    ))
                if "reading_order" in label:
                    facts.extend(score_reading_order(
                        extract_markdown(engine_id, run_dir / "native"),
                        label["reading_order"],
                    ))
                if "figures" in label:
                    markdown = extract_markdown(engine_id, run_dir / "native")
                    facts.extend(score_figures(
                        extract_figures(engine_id, run_dir / "native"),
                        label["figures"],
                        markdown,
                    ))
                if "equations" in label:
                    facts.extend(score_equations(
                        extract_equations(engine_id, run_dir / "native"),
                        label["equations"],
                    ))
                if not facts:
                    raise ValueError("labels contain no supported facts")
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                failures += 1
                print(f"[FAIL] {document_id}/{engine_id}: {exc}")
                results.append({
                    "document": document_id,
                    "engine": engine_id,
                    "passed": False,
                    "error": str(exc),
                })
                continue

            passed = sum(ok for ok, _ in facts)
            scored_facts += len(facts)
            failures += len(facts) - passed
            mark = "ok" if passed == len(facts) else "FAIL"
            print(f"[{mark}] {document_id}/{engine_id}: {passed}/{len(facts)} facts")
            for ok, description in facts:
                if not ok:
                    print(f"       FAIL: {description}")
            results.append({
                "document": document_id,
                "engine": engine_id,
                "run_id": run["run_id"],
                "passed": passed == len(facts),
                "duration_seconds": run.get("duration_seconds"),
                "resources": run.get("resources"),
                "source_sha256": run.get("source", {}).get("sha256"),
                "input_sha256": run.get("input", {}).get("sha256"),
                "engine_provenance": {
                    "version_probe": _portable_version_probe(run),
                    "layout_candidate": run.get("engine", {}).get("layout_candidate"),
                },
                "candidate_provenance": _portable_candidate_provenance(run_dir),
                "declared_engine_pin": _declared_engine_pin(engine_id),
                "facts": [
                    {"passed": ok, "description": description}
                    for ok, description in facts
                ],
            })

    if args.json_path:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(json.dumps({
            "schema_version": 1,
            "labels": {
                "path": _portable_path(args.labels),
                "sha256": _sha256(args.labels),
            },
            "results": results,
        }, indent=2) + "\n")
    if (args.strict or args.check) and (failures or scored_facts == 0):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
