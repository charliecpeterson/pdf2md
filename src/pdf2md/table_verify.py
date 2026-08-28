"""Attach independent-reader and external-reference evidence to extracted table cells.

The OCR candidate is never rewritten. Each JSONL record keeps the raw engine value,
the second reader's value when available, and an explicit comparison outcome.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import shutil
import statistics
import subprocess
import tempfile
from collections import Counter
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path

from PIL import Image

from pdf2md.logging import Progress, get_logger
from pdf2md.scan_deskew import deskew_image
from pdf2md.schema import Block, TableData
from pdf2md.table_reference import (
    compare_external_reference,
    external_outcome as _external_outcome,
    load_external_reference as _load_external_reference,
    normalized_semantics as _normalized_semantics,
)
from pdf2md.table_resolution import resolve_cell_records
from pdf2md.tables import RepeatedPanelLayout, split_repeated_panels

# A numeric grid whose mapped tokens are mostly nonnumeric is structurally misaligned.
# This gate does not compare values with the engine, so real disagreements remain visible.
_MIN_NUMERIC_PARSE_RATE = 0.5
log = get_logger("table_verify")


def typed_value(raw_value: str) -> tuple[str, str, str]:
    stripped = raw_value.strip()
    if not stripped:
        return "", "", "blank"
    if "=" in stripped:
        _, _, inline_value = stripped.partition("=")
        if inline_value.strip():
            _, numeric_value, value_status = typed_value(inline_value)
            if value_status == "numeric":
                return inline_value.strip(), numeric_value, value_status
    if stripped == ".":
        return "", "", "dot_placeholder"
    if stripped in {"-", "−", "–", "—"}:
        return "", "", "dash_placeholder"
    normalized = stripped.replace("−", "-").replace("–", "-").replace("—", "-")
    normalized = _compact_numeric_spacing(normalized)
    if "," in normalized:
        if not re.fullmatch(r"[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", normalized):
            return stripped, "", "text"
        normalized = normalized.replace(",", "")
    numeric_value = normalized.replace("D", "E").replace("d", "e")
    try:
        float(numeric_value)
    except ValueError:
        return stripped, "", "text"
    return stripped, numeric_value, "numeric"


def _compact_numeric_spacing(value: str) -> str:
    candidate = re.sub(r"\s*\.\s*", ".", value)
    footnote = re.fullmatch(r"(.+\d)\s+[A-Za-z]", candidate)
    if footnote and "." in candidate:
        candidate = footnote.group(1)
    if not re.search(r"\s", candidate):
        return candidate
    grouped = re.fullmatch(
        r"[-+]?(?:\d{1,3}(?:\s+\d{3})+|\d+)"
        r"(?:\.\d+(?:\s+\d{1,3})*)?(?:[eEdD][-+]?\d+)?",
        candidate,
    )
    return re.sub(r"\s+", "", candidate) if grouped else value


def write_cell_evidence(
    tables: list[TableData],
    rows_by_block: dict[str, list[list[str]]],
    blocks: dict[str, Block],
    version_dir: Path,
    tesseract_executable: str | None = None,
    reference_path: str | None = None,
    progress: Progress | None = None,
) -> dict[str, int]:
    """Write one evidence JSONL per source table and return aggregate outcome counts."""
    executable = shutil.which(tesseract_executable) if tesseract_executable else None
    references = _load_external_reference(Path(reference_path)) if reference_path else {}
    semantics = _normalized_semantics(tables, version_dir)
    totals: Counter[str] = Counter()
    page_layouts: dict[int, RepeatedPanelLayout] = {}
    candidates = [
        table for table in tables
        if rows_by_block.get(table.block_id, []) and table.json_path
    ]
    progress = progress or Progress(log)
    if candidates and tesseract_executable:
        progress.count(
            "verifying tables", 0, len(candidates), unit="tables", force=True
        )

    for completed, table in enumerate(candidates, start=1):
        rows = rows_by_block.get(table.block_id, [])
        block = blocks.get(table.block_id)
        mapped = None
        refusal = None
        reader_rotation = None
        reader_deskew_degrees = 0.0
        layout = _table_layout(rows, page_layouts.get(table.page))
        if layout is not None:
            page_layouts[table.page] = layout
        if tesseract_executable and executable is None:
            refusal = "tesseract_not_found"
        elif executable:
            crop_path = version_dir / str(block.extra.get("crop_path", "")) if block else None
            if crop_path is None or not crop_path.is_file():
                refusal = "crop_unavailable"
            elif layout is None:
                refusal = "grid_layout_unavailable"
            else:
                mapped, refusal, reader_rotation, reader_deskew_degrees = _read_tesseract(
                    rows, crop_path, executable, layout
                )

        stem = table.block_id.strip("#/").replace("/", "_")
        evidence_path = version_dir / "data" / "tables" / f"{stem}.cells.jsonl"
        counts: Counter[str] = Counter()
        records = []
        for row_index, row in enumerate(rows):
            for column_index, raw_value in enumerate(row):
                value, numeric_value, value_status = typed_value(raw_value)
                semantic = semantics.get((table.block_id, row_index, column_index), {})
                reader_value = mapped.get((row_index, column_index)) if mapped else None
                reader_outcome = _reader_outcome(
                    raw_value, value_status, reader_value, bool(tesseract_executable), refusal
                )
                external_value, external_outcome = _external_outcome(
                    semantic, raw_value, references, bool(reference_path)
                )
                verification_status = _verification_status(
                    reader_outcome, external_outcome, value_status
                )
                records.append({
                    "schema_version": 2,
                    "page": table.page,
                    "source_block_id": table.block_id,
                    "source_row": row_index,
                    "source_column": column_index,
                    "raw_value": raw_value,
                    "value": value,
                    "numeric_value": numeric_value or None,
                    "value_status": value_status,
                    "reader": "tesseract" if tesseract_executable else None,
                    "reader_rotation": reader_rotation,
                    "reader_deskew_degrees": reader_deskew_degrees,
                    "reader_value": reader_value,
                    "reader_outcome": reader_outcome,
                    "reader_refusal_reason": (
                        (refusal or "cell_alignment_missing")
                        if reader_outcome == "reader_refused"
                        else None
                    ),
                    "external_reference_value": external_value,
                    "external_outcome": external_outcome,
                    "verification_status": verification_status,
                    "semantic_key": semantic or None,
                })
        resolve_cell_records(records, rows, layout)
        with evidence_path.open("w") as stream:
            for record in records:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                counts[str(record["verification_status"])] += 1
                totals[str(record["verification_status"])] += 1
        table.cell_evidence_path = evidence_path.relative_to(version_dir).as_posix()
        table.cell_evidence_counts = dict(counts)
        table.cell_resolution_counts = dict(Counter(
            str(record["confidence"]) for record in records
        ))
        if tesseract_executable:
            progress.count(
                "verifying tables",
                completed,
                len(candidates),
                unit="tables",
                detail=f"page {table.page}",
            )
    return dict(totals)


def _table_layout(
    rows: list[list[str]], previous: RepeatedPanelLayout | None
) -> RepeatedPanelLayout | None:
    _, layout = split_repeated_panels(rows)
    if layout is not None:
        return layout
    if previous is not None and len(previous.starts) > 1:
        panels, layout = split_repeated_panels(rows, previous)
        if panels and layout is not None:
            return layout
    width = max((len(row) for row in rows), default=0)
    if not width:
        return None
    header = next(
        (
            row for row in rows
            if any(cell.strip().upper() in {"NL", "RADIUS"} for cell in row)
        ),
        [],
    )
    if not header:
        header = [
            "numeric" if any(
                column < len(row)
                and (
                    typed_value(row[column])[2]
                    in {"numeric", "dot_placeholder", "dash_placeholder"}
                    or any(character.isdigit() for character in row[column])
                )
                for row in rows
            ) else ""
            for column in range(width)
        ]
    return RepeatedPanelLayout((0,), width, ("",), (tuple(header),))


def _reader_outcome(
    engine_value: str,
    value_status: str,
    reader_value: str | None,
    requested: bool,
    refusal: str | None,
) -> str:
    if value_status != "numeric":
        return "not_applicable"
    if not requested:
        return "single_reader"
    if refusal or reader_value is None:
        return "reader_refused"
    return "agree" if numeric_values_equal(engine_value, reader_value) else "disagree"


def _verification_status(
    reader_outcome: str, external_outcome: str, value_status: str
) -> str:
    if external_outcome == "agree":
        return "externally_verified"
    if external_outcome == "disagree":
        return "external_disagreement"
    if value_status != "numeric":
        return "not_applicable"
    if reader_outcome == "agree":
        return "reader_agreement"
    if reader_outcome == "disagree":
        return "reader_disagreement"
    if reader_outcome == "reader_refused":
        return "reader_refused"
    return "candidate"


def _numeric_read(text: str) -> str:
    normalized = text.strip().translate(str.maketrans({
        "O": "0", "o": "0", "Q": "0", "q": "0", ",": ".", "−": "-", "–": "-",
        "—": "-", "D": "E", "d": "e",
    }))
    normalized = _compact_numeric_spacing(normalized)
    if re.fullmatch(r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)\.", normalized):
        normalized = normalized[:-1]
    return normalized


def numeric_values_equal(left: str, right: str) -> bool:
    try:
        return Decimal(_numeric_read(left)) == Decimal(_numeric_read(right))
    except InvalidOperation:
        return _numeric_read(left) == _numeric_read(right)


def _read_tesseract(
    rows: list[list[str]], crop_path: Path, executable: str, layout: RepeatedPanelLayout
) -> tuple[dict[tuple[int, int], str] | None, str | None, int | None, float]:
    with tempfile.TemporaryDirectory(prefix="pdf2md-table-") as temp_dir:
        with Image.open(crop_path) as image:
            image, deskew_degrees = deskew_image(image)
        reader_path = crop_path
        if deskew_degrees:
            reader_path = Path(temp_dir) / "deskewed.png"
            image.save(reader_path)

        mapped, refusal = _run_tesseract(rows, reader_path, executable, layout)
        if (
            mapped is not None
            and _reader_parse_rate(rows, mapped) >= _MIN_NUMERIC_PARSE_RATE
            and _reader_coverage_rate(rows, mapped) >= _MIN_NUMERIC_PARSE_RATE
        ):
            return mapped, None, 0, deskew_degrees
        if mapped is not None:
            refusal = "grid_alignment_failed"
        if refusal != "grid_alignment_failed":
            return None, refusal, None, deskew_degrees

        rotated_reads = []
        for rotation in (90, 270):
            rotated_path = Path(temp_dir) / f"rotated_{rotation}.png"
            image.rotate(rotation, expand=True, fillcolor="white").save(rotated_path)
            rotated, _ = _run_tesseract(rows, rotated_path, executable, layout)
            if rotated is None:
                continue
            parse_rate = _reader_parse_rate(rows, rotated)
            coverage_rate = _reader_coverage_rate(rows, rotated)
            if (
                parse_rate >= _MIN_NUMERIC_PARSE_RATE
                and coverage_rate >= _MIN_NUMERIC_PARSE_RATE
            ):
                rotated_reads.append((
                    (
                        coverage_rate,
                        parse_rate,
                        *_reader_agreement_score(rows, rotated),
                    ),
                    rotation,
                    rotated,
                ))
        if not rotated_reads:
            return None, refusal, None, deskew_degrees
        _, rotation, mapped = max(rotated_reads, key=lambda item: item[0])
        return mapped, None, rotation, deskew_degrees


def _run_tesseract(
    rows: list[list[str]], crop_path: Path, executable: str, layout: RepeatedPanelLayout
) -> tuple[dict[tuple[int, int], str] | None, str | None]:
    try:
        completed = subprocess.run(
            [executable, str(crop_path), "stdout", "--psm", "6", "-l", "eng", "tsv"],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return None, "tesseract_timeout"
    if completed.returncode:
        return None, f"tesseract_exit_{completed.returncode}"
    mapped = map_tesseract_tsv(rows, completed.stdout, layout)
    return (mapped, None) if mapped is not None else (None, "grid_alignment_failed")


def _reader_agreement_score(
    rows: list[list[str]], mapped: dict[tuple[int, int], str]
) -> tuple[float, int]:
    comparisons = [
        numeric_values_equal(rows[row][column], value)
        for (row, column), value in mapped.items()
        if row < len(rows)
        and column < len(rows[row])
        and typed_value(rows[row][column])[2] == "numeric"
    ]
    if not comparisons:
        return 0.0, 0
    return sum(comparisons) / len(comparisons), len(comparisons)


def _reader_parse_rate(
    rows: list[list[str]], mapped: dict[tuple[int, int], str]
) -> float:
    readings = [
        value
        for (row, column), value in mapped.items()
        if row < len(rows)
        and column < len(rows[row])
        and typed_value(rows[row][column])[2] == "numeric"
    ]
    if not readings:
        return 0.0
    parsed = 0
    for value in readings:
        try:
            float(_numeric_read(value))
        except ValueError:
            continue
        parsed += 1
    return parsed / len(readings)


def _reader_coverage_rate(
    rows: list[list[str]], mapped: dict[tuple[int, int], str]
) -> float:
    numeric_cells = [
        (row_index, column_index)
        for row_index in _numeric_source_rows(rows)
        for column_index, value in enumerate(rows[row_index])
        if typed_value(value)[2] == "numeric"
    ]
    if not numeric_cells:
        return 0.0
    return sum(cell in mapped for cell in numeric_cells) / len(numeric_cells)


def map_tesseract_tsv(
    rows: list[list[str]], tsv: str, layout: RepeatedPanelLayout
) -> dict[tuple[int, int], str] | None:
    aligned = _aligned_tesseract_lines(rows, tsv, layout)
    if not aligned:
        return None
    aligned_rows = [row_index for row_index, _ in aligned]
    aligned_lines = [line for _, line in aligned]
    center_lines = [
        line for row_index, line in aligned
        if not _is_property_row(rows[row_index])
    ] or aligned_lines
    centers = _column_centers(center_lines, layout)
    if (centers is None or _centers_are_collapsed(centers)) and len(layout.starts) == 1:
        numeric_columns = tuple(
            "numeric" if any(
                column < len(row)
                and typed_value(row[column])[2]
                in {"numeric", "dot_placeholder", "dash_placeholder"}
                for row in rows
            ) else ""
            for column in range(layout.width)
        )
        numeric_layout = RepeatedPanelLayout(
            layout.starts,
            layout.width,
            layout.titles,
            (numeric_columns,),
            layout.widths,
            layout.title_cells,
        )
        revised = _column_centers(center_lines, numeric_layout)
        if revised is not None and not _centers_are_collapsed(revised):
            centers = revised
    if centers is None:
        return None

    mapped = {}
    for row_index, line in zip(aligned_rows, aligned_lines):
        if _is_property_row(rows[row_index]):
            source_columns = [
                column_index
                for column_index, value in enumerate(rows[row_index])
                if typed_value(value)[2] == "numeric"
            ]
            reader_words = sorted(
                (word for word in line if _looks_numeric(str(word["text"]))),
                key=lambda word: float(word["x"]),
            )
            if source_columns and len(source_columns) == len(reader_words):
                for column, word in zip(source_columns, reader_words):
                    mapped[row_index, column] = _numeric_read(str(word["text"]))
            continue
        center_values = [center for _, center in centers]
        for center_index, (column, center) in enumerate(centers):
            word = min(line, key=lambda item: abs(item["x"] - center))
            gaps = []
            if center_index:
                gaps.append(center - center_values[center_index - 1])
            if center_index + 1 < len(centers):
                gaps.append(center_values[center_index + 1] - center)
            if not gaps or abs(word["x"] - center) <= min(gaps) * 0.45:
                mapped[row_index, column] = _numeric_read(word["text"])
    return mapped


def _aligned_tesseract_lines(
    rows: list[list[str]], tsv: str, layout: RepeatedPanelLayout
) -> list[tuple[int, list[dict[str, object]]]]:
    numeric_rows = _numeric_source_rows(rows)
    lines = _word_lines(tsv)
    total_width = sum(layout.widths) if layout.widths else layout.width * len(layout.starts)
    table_i = any(cell.strip().upper() == "NL" for row in rows for cell in row)
    minimum = 1 if table_i else max(1, min(4, math.ceil(total_width / 3)))
    candidates = [
        line for line in lines
        if sum(_numericish_word(str(word["text"])) for word in line) >= minimum
    ]
    return _align_numeric_lines(rows, numeric_rows, candidates)


def _numeric_source_rows(rows: list[list[str]]) -> list[int]:
    selected = []
    seen_properties: set[tuple[str, ...]] = set()
    for row_index, row in enumerate(rows):
        numeric = sum(
            typed_value(cell)[2] in {"numeric", "dot_placeholder", "dash_placeholder"}
            for cell in row
        )
        property_row = _is_property_row(row)
        if numeric < max(1, math.ceil(len(row) / 3)) and not (property_row and numeric):
            continue
        signature = tuple(row)
        if property_row and signature in seen_properties:
            continue
        if property_row:
            seen_properties.add(signature)
        selected.append(row_index)
    return selected


def _is_property_row(row: list[str]) -> bool:
    return any(
        cell.strip().endswith("=")
        or ("=" in cell and typed_value(cell)[2] == "numeric")
        for cell in row
    )


def _align_numeric_lines(
    rows: list[list[str]],
    numeric_rows: list[int],
    lines: list[list[dict[str, object]]],
) -> list[tuple[int, list[dict[str, object]]]]:
    """Monotonically align table rows while allowing OCR-only and missed lines."""
    if not numeric_rows or not lines:
        return []
    expected = [
        sum(
            typed_value(cell)[2] in {"numeric", "dot_placeholder", "dash_placeholder"}
            for cell in rows[row_index]
        )
        for row_index in numeric_rows
    ]
    observed = [sum(_numericish_word(str(word["text"])) for word in line) for line in lines]
    source_count = len(numeric_rows)
    line_count = len(lines)
    infinity = float("inf")
    costs = [[infinity] * (line_count + 1) for _ in range(source_count + 1)]
    actions: list[list[str | None]] = [
        [None] * (line_count + 1) for _ in range(source_count + 1)
    ]
    costs[0][0] = 0.0
    for source_index in range(source_count + 1):
        for line_index in range(line_count + 1):
            cost = costs[source_index][line_index]
            if cost == infinity:
                continue
            if source_index < source_count and cost + 2.5 < costs[source_index + 1][line_index]:
                costs[source_index + 1][line_index] = cost + 2.5
                actions[source_index + 1][line_index] = "skip_source"
            if line_index < line_count and cost + 1.0 < costs[source_index][line_index + 1]:
                costs[source_index][line_index + 1] = cost + 1.0
                actions[source_index][line_index + 1] = "skip_line"
            if source_index < source_count and line_index < line_count:
                scale = max(expected[source_index], observed[line_index], 1)
                count_cost = 3 * abs(expected[source_index] - observed[line_index]) / scale
                position_cost = abs(
                    (source_index + 0.5) / source_count
                    - (line_index + 0.5) / line_count
                )
                label_cost = _row_label_cost(
                    rows[numeric_rows[source_index]], lines[line_index]
                )
                match_cost = cost + count_cost + position_cost + label_cost
                if match_cost < costs[source_index + 1][line_index + 1]:
                    costs[source_index + 1][line_index + 1] = match_cost
                    actions[source_index + 1][line_index + 1] = "match"

    aligned = []
    source_index, line_index = source_count, line_count
    while source_index or line_index:
        action = actions[source_index][line_index]
        if action == "match":
            aligned.append((numeric_rows[source_index - 1], lines[line_index - 1]))
            source_index -= 1
            line_index -= 1
        elif action == "skip_source":
            source_index -= 1
        elif action == "skip_line":
            line_index -= 1
        else:
            return []
    aligned.reverse()
    if len(aligned) < math.ceil(source_count * _MIN_NUMERIC_PARSE_RATE):
        return []
    return aligned


def _row_label_cost(
    row: list[str], line: list[dict[str, object]]
) -> float:
    if _is_property_row(row):
        return 0.0
    label = row[0].strip() if row else ""
    if typed_value(label)[2] == "numeric":
        observed = next(
            (str(word["text"]) for word in line if _looks_numeric(str(word["text"]))),
            "",
        )
        observed_key = observed.translate(str.maketrans({
            "C": "0", "c": "0", "I": "1", "L": "1", "l": "1", "|": "1",
        }))
        return 0.0 if _numeric_read(label) == _numeric_read(observed_key) else 4.0
    labels = []
    for cell in row:
        if typed_value(cell)[2] in {
            "numeric", "dot_placeholder", "dash_placeholder"
        }:
            break
        if re.search(r"[A-Za-z]", cell):
            labels.append(re.sub(r"[^a-z0-9]", "", cell.lower()))
    if not labels:
        return 0.0
    observed = re.sub(
        r"[^a-z0-9]", "", " ".join(str(word["text"]) for word in line).lower()
    )
    if not observed:
        return 0.0
    similarity = max(
        SequenceMatcher(None, expected, observed, autojunk=False)
        .find_longest_match().size / len(expected)
        for expected in labels
    )
    return 4.0 * (1.0 - similarity)


def _is_number(text: str) -> bool:
    try:
        float(text.replace(",", ""))
    except ValueError:
        return False
    return True


def _looks_numeric(text: str) -> bool:
    digits = re.sub(r"\D", "", text)
    return bool(digits) and ("." in text or "," in text or len(digits) >= 3)


def _numericish_word(text: str) -> bool:
    return _looks_numeric(text) or typed_value(_numeric_read(text))[2] == "numeric"


def _word_lines(tsv: str) -> list[list[dict[str, object]]]:
    groups: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for raw in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if raw.get("level") != "5" or not raw.get("text", "").strip():
            continue
        word = {
            "text": raw["text"].strip(),
            "left": int(raw["left"]),
            "top": int(raw["top"]),
            "width": int(raw["width"]),
            "height": int(raw["height"]),
            "x": int(raw["left"]) + int(raw["width"]) / 2,
            "y": int(raw["top"]) + int(raw["height"]) / 2,
        }
        key = (raw["block_num"], raw["par_num"], raw["line_num"])
        groups.setdefault(key, []).append(word)
    return sorted(groups.values(), key=lambda words: statistics.median(word["y"] for word in words))


def _cluster_centers(values: list[float], count: int) -> list[float] | None:
    if count < 1 or len(values) < count:
        return None
    low, high = min(values), max(values)
    centers = [low + (high - low) * index / max(count - 1, 1) for index in range(count)]
    for _ in range(50):
        bins = [[] for _ in centers]
        for value in values:
            nearest = min(range(count), key=lambda index: abs(value - centers[index]))
            bins[nearest].append(value)
        if any(not bucket for bucket in bins):
            return None
        updated = [statistics.median(bucket) for bucket in bins]
        if updated == centers:
            break
        centers = updated
    return centers


def _centers_are_collapsed(centers: list[tuple[int, float]] | None) -> bool:
    if centers is None or len(centers) < 3:
        return False
    gaps = [right[1] - left[1] for left, right in zip(centers, centers[1:])]
    return min(gaps) < statistics.median(gaps) * 0.35


def _column_centers(
    lines: list[list[dict[str, object]]], layout: RepeatedPanelLayout
) -> list[tuple[int, float]] | None:
    xs = [
        word["x"] for line in lines for word in line
        if any(character.isdigit() for character in word["text"])
        or word["text"] in {".", "-"}
    ]
    panel_count = len(layout.starts)
    if not xs:
        return None
    unique = sorted(set(xs))
    if panel_count > 1 and len(unique) < panel_count:
        return None
    gaps = sorted(
        ((right - left, (left + right) / 2) for left, right in zip(unique, unique[1:])),
        reverse=True,
    )
    cuts = sorted(midpoint for _, midpoint in gaps[:panel_count - 1])
    centers = []
    for panel_index in range(panel_count):
        left = cuts[panel_index - 1] if panel_index else -math.inf
        right = cuts[panel_index] if panel_index < len(cuts) else math.inf
        panel_xs = [value for value in xs if left < value < right]
        panel_columns = (
            layout.columns[panel_index] if panel_index < len(layout.columns) else ()
        )
        panel_source_columns = [
            layout.starts[panel_index] + index
            for index in range(layout.panel_width(panel_index))
            if not panel_columns
            or (index < len(panel_columns) and panel_columns[index].strip())
        ]
        panel_centers = _cluster_centers(panel_xs, len(panel_source_columns))
        if panel_centers is None:
            return None
        centers.extend(zip(panel_source_columns, panel_centers))
    return centers
