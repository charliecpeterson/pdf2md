"""Choose a consumer-facing table value without discarding OCR evidence.

External references and reader agreement are decisive. Format and local-continuity
rules remain diagnostic because legitimate scientific structure can violate them.
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from pathlib import Path

from pdf2md.schema import TableData
from pdf2md.tables import RepeatedPanelLayout


RESOLUTION_FIELDS = [
    "primary_value",
    "reader_value",
    "best_value",
    "confidence",
    "resolution_basis",
    "validator_preference",
    "validator_basis",
    "verification_status",
    "reader_refusal_reason",
]


def resolve_cell_records(
    records: list[dict[str, object]],
    rows: list[list[str]],
    layout: RepeatedPanelLayout | None,
) -> list[dict[str, object]]:
    for record in records:
        record["validator_preference"] = None
        record["validator_basis"] = None
        status = str(record["value_status"])
        primary = (
            str(record.get("numeric_value") or record["value"])
            if status == "numeric"
            else str(record["value"])
        )
        record["primary_value"] = primary
        if status != "numeric":
            record["best_value"] = primary
            record["confidence"] = "not_applicable"
            record["resolution_basis"] = status
            continue

        external = record.get("external_reference_value")
        external_outcome = record.get("external_outcome")
        if external is not None and external_outcome in {"agree", "disagree"}:
            record["best_value"] = str(external)
            record["confidence"] = "verified"
            record["resolution_basis"] = (
                "external_reference_agreement"
                if external_outcome == "agree"
                else "external_reference_override"
            )
            continue

        reader_outcome = record.get("reader_outcome")
        if reader_outcome == "agree":
            record["best_value"] = primary
            record["confidence"] = "high"
            record["resolution_basis"] = "independent_reader_agreement"
            continue
        if reader_outcome != "disagree":
            record["best_value"] = primary
            record["confidence"] = "low"
            record["resolution_basis"] = (
                "reader_refused_primary_retained"
                if reader_outcome == "reader_refused"
                else "single_reader_primary_retained"
            )
            continue

        reader = str(record.get("reader_value") or "")
        decision = _resolve_disagreement(record, rows, layout, primary, reader)
        if decision is not None:
            record["validator_preference"], record["validator_basis"] = decision
        record["best_value"] = primary
        record["confidence"] = "low"
        record["resolution_basis"] = "reader_disagreement_primary_retained"
    return records


def enrich_normalized_datasets(tables: list[TableData], version_dir: Path) -> None:
    evidence = {}
    for table in tables:
        if not table.cell_evidence_path:
            continue
        path = version_dir / table.cell_evidence_path
        for line in path.read_text().splitlines():
            record = json.loads(line)
            evidence[(
                record["source_block_id"],
                record["source_row"],
                record["source_column"],
            )] = record

    datasets = {
        (table.normalized_data_path, table.normalized_json_path)
        for table in tables
        if table.normalized_data_path and table.normalized_json_path
    }
    for csv_relative, json_relative in datasets:
        csv_path = version_dir / csv_relative
        with csv_path.open(newline="") as stream:
            reader = csv.DictReader(stream)
            fieldnames = list(reader.fieldnames or [])
            records = list(reader)
        for field in RESOLUTION_FIELDS:
            if field not in fieldnames:
                fieldnames.append(field)
        for record in records:
            source = evidence.get((
                record["source_block_id"],
                int(record["source_row"]),
                int(record["source_column"]),
            ))
            for field in RESOLUTION_FIELDS:
                record[field] = "" if source is None or source.get(field) is None else source[field]
        with csv_path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records)

        json_path = version_dir / json_relative
        dataset = json.loads(json_path.read_text())
        dataset["schema_version"] = 5
        dataset["records"] = records
        json_path.write_text(json.dumps(dataset, indent=2, ensure_ascii=False) + "\n")


def _resolve_disagreement(
    record: dict[str, object],
    rows: list[list[str]],
    layout: RepeatedPanelLayout | None,
    primary_text: str,
    reader_text: str,
) -> tuple[str, str] | None:
    primary = _number(primary_text)
    reader = _number(reader_text)
    if primary is not None and reader is None:
        return "primary", "reader_not_numeric"
    if primary is None or reader is None:
        return None

    row_index = int(record["source_row"])
    column_index = int(record["source_column"])
    key_column = _key_column(column_index, layout)
    primary_score = _continuity_score(rows, row_index, column_index, key_column, primary)
    reader_score = _continuity_score(rows, row_index, column_index, key_column, reader)
    if primary_score is not None and reader_score is not None:
        if primary_score <= 2 and reader_score >= max(4, primary_score * 5):
            return "primary", "local_continuity_primary"
        if reader_score <= 2 and primary_score >= max(4, reader_score * 5):
            return "reader", "local_continuity_reader"

    expected_places = _dominant_decimal_places(rows, column_index)
    if expected_places is not None:
        primary_matches = _decimal_places(primary_text) == expected_places
        reader_matches = _decimal_places(reader_text) == expected_places
        if primary_matches != reader_matches:
            choice = "primary" if primary_matches else "reader"
            return choice, f"column_decimal_format_{choice}"
    return None


def _key_column(column_index: int, layout: RepeatedPanelLayout | None) -> int:
    if layout is None:
        return 0
    for panel_index, start in enumerate(layout.starts):
        if start <= column_index < start + layout.panel_width(panel_index):
            return start
    return 0


def _continuity_score(
    rows: list[list[str]],
    row_index: int,
    column_index: int,
    key_column: int,
    candidate: float,
) -> float | None:
    if row_index >= len(rows) or key_column >= len(rows[row_index]):
        return None
    current_key = _number(rows[row_index][key_column])
    if current_key is None:
        return None

    neighbors = []
    for direction in (-1, 1):
        for distance in range(1, 5):
            neighbor_index = row_index + direction * distance
            if not 0 <= neighbor_index < len(rows):
                break
            row = rows[neighbor_index]
            if max(key_column, column_index) >= len(row):
                continue
            key = _number(row[key_column])
            value = _number(row[column_index])
            if key is not None and value is not None:
                neighbors.append((direction, key, value))
                break
    before = next((item for item in neighbors if item[0] == -1), None)
    after = next((item for item in neighbors if item[0] == 1), None)
    if before is None or after is None or after[1] == before[1]:
        return None
    fraction = (current_key - before[1]) / (after[1] - before[1])
    expected = before[2] + fraction * (after[2] - before[2])
    scale = max(abs(after[2] - before[2]), abs(expected) * 0.02, 1e-12)
    return abs(candidate - expected) / scale


def _dominant_decimal_places(rows: list[list[str]], column_index: int) -> int | None:
    places = Counter()
    numeric = 0
    for row in rows:
        if column_index >= len(row) or _number(row[column_index]) is None:
            continue
        numeric += 1
        decimal_places = _decimal_places(row[column_index])
        if decimal_places is not None:
            places[decimal_places] += 1
    if numeric < 8 or not places:
        return None
    expected, count = places.most_common(1)[0]
    return expected if count / numeric >= 0.8 else None


def _decimal_places(text: str) -> int | None:
    normalized = text.strip().lower()
    if "e" in normalized:
        return None
    match = re.fullmatch(r"[-+]?\d+(?:\.(\d+))?", normalized)
    if match is None:
        return None
    return len(match.group(1) or "")


def _number(text: str) -> float | None:
    normalized = text.strip().translate(str.maketrans({
        "O": "0", "o": "0", "Q": "0", "q": "0", ",": ".", "−": "-", "–": "-",
        "—": "-",
    }))
    try:
        value = float(normalized)
    except ValueError:
        return None
    return value if math.isfinite(value) else None
