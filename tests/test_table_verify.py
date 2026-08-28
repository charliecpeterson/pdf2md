"""Typed table values and independent external-reference outcomes."""

from __future__ import annotations

import csv
import json
import re

from pdf2md import table_verify
from pdf2md.emit import emit_document
from pdf2md.schema import Block, BlockType, Document, TableData
from pdf2md.structure import build_structure
from pdf2md.table_resolution import resolve_cell_records
from pdf2md.table_verify import (
    _aligned_tesseract_lines,
    _table_layout,
    _reader_parse_rate,
    _verification_status,
    compare_external_reference,
    map_tesseract_tsv,
    typed_value,
)


def _disagreement_record(primary: str, reader: str, row: int = 1, column: int = 1):
    return {
        "value": primary,
        "value_status": "numeric",
        "external_reference_value": None,
        "external_outcome": "no_reference",
        "reader_outcome": "disagree",
        "reader_value": reader,
        "source_row": row,
        "source_column": column,
    }


def _table_i_document():
    block = Block("#/table", BlockType.TABLE, "", 1, extra={"ocr": True})
    table = TableData(
        block.id,
        1,
        None,
        "\n".join([
            "| 44 | RU | 5D | (4D)6 | | | | | |",
            "|---|---|---|---|---|---|---|---|---|",
            "| NL | E | | A | S | R | R**2 | 1/R | 1/R**3 |",
            "| 1S | 1.2500 | | . | - | 0.1000 | 0.0100 | 2.0000 | 3.0000 |",
            "| 45 | RH | 4F | (4D)7 | | | | | |",
            "| NL | E | | A | S | R | R**2 | 1/R | 1/R**3 |",
            "| 1S | 1.3500 | | 4.0000 | - | 0.2000 | 0.0200 | 2.1000 | 3.1000 |",
        ]),
    )
    structure = build_structure([block], None, title="Doc", page_count=1)
    return Document(
        "a" * 64,
        "/source.pdf",
        "a" * 64,
        1,
        1,
        structure.root,
        blocks=[block],
        tables=[table],
    ), structure, table


def test_typed_value_preserves_raw_placeholders():
    assert typed_value("1,234.50") == ("1,234.50", "1234.50", "numeric")
    assert typed_value("1,25") == ("1,25", "", "text")
    assert typed_value(".") == ("", "", "dot_placeholder")
    assert typed_value("-") == ("", "", "dash_placeholder")
    assert typed_value("—") == ("", "", "dash_placeholder")
    assert typed_value("") == ("", "", "blank")
    assert typed_value("-2 846.292") == ("-2 846.292", "-2846.292", "numeric")
    assert typed_value("-14.556 089") == ("-14.556 089", "-14.556089", "numeric")
    assert typed_value("-14.667 354 7 b") == (
        "-14.667 354 7 b", "-14.6673547", "numeric"
    )
    assert typed_value("-0.28567D-02") == (
        "-0.28567D-02", "-0.28567E-02", "numeric"
    )
    assert typed_value("0.6356SD-03") == ("0.6356SD-03", "", "text")
    assert typed_value("1 2") == ("1 2", "", "text")


def test_numeric_reader_ignores_typographic_digit_grouping():
    assert table_verify._numeric_read("-14.556 089") == "-14.556089"
    assert table_verify._numeric_read("-2 846.292") == "-2846.292"
    assert _verification_status("agree", "no_reference", "numeric") == "reader_agreement"
    assert _verification_status("disagree", "no_reference", "numeric") == "reader_disagreement"
    assert _verification_status("reader_refused", "no_reference", "numeric") == "reader_refused"
    assert table_verify._numeric_read("30.7.") == "30.7"
    assert table_verify.numeric_values_equal("6.150", "6.15")
    assert table_verify.numeric_values_equal("-0.28567D-02", "-0.28567E-02")


def test_tesseract_mapping_skips_table_i_spacer_column():
    rows = [
        ["44", "RU", "5D", "(4D)6", "", "", "", "", ""],
        ["NL", "E", "", "A", "S", "R", "R**2", "1/R", "1/R**3"],
        ["1S", "1.25", "", "2.0", "3.0", "4.0", "5.0", "6.0", "7.0"],
        ["TOTAL ENERGY =", "", "-4441.488", "", "", "", "", "", ""],
    ]
    layout = _table_layout(rows, None)
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    lines = [
        "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t90\t44",
        "5\t1\t1\t1\t2\t1\t0\t20\t10\t10\t90\tNL",
        *[
            f"5\t1\t1\t1\t3\t{index + 1}\t{x}\t40\t10\t10\t90\t{text}"
            for index, (x, text) in enumerate(zip(
                range(0, 160, 20),
                ["1S", "1.25", "2.0", "3.0", "4.0", "5.0", "6.0", "7.0"],
            ))
        ],
        "5\t1\t1\t1\t4\t1\t40\t60\t10\t10\t90\t-4441.488",
    ]

    mapped = map_tesseract_tsv(rows, "\n".join([header, *lines]), layout)

    assert mapped[2, 1] == "1.25"
    assert (2, 2) not in mapped
    assert mapped[2, 8] == "7.0"
    assert mapped[3, 2] == "-4441.488"


def test_tesseract_mapping_skips_an_extra_numeric_ocr_line():
    rows = [
        ["LEFT", "", "RIGHT", ""],
        ["R", "X", "R", "X"],
        ["0.1", "1.2", "0.1", "2.2"],
        ["0.2", "1.4", "0.2", "2.4"],
    ]
    layout = _table_layout(rows, None)
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    words = [
        "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t90\tLEFT",
        "5\t1\t1\t1\t2\t1\t0\t20\t10\t10\t90\tR",
    ]
    for line_number, values in enumerate([
        ["0.1", "1.2", "0.1", "2.2"],
        ["99.0", "98.0", "97.0", "96.0"],
        ["0.2", "1.4", "0.2", "2.4"],
    ], start=3):
        words.extend(
            f"5\t1\t1\t1\t{line_number}\t{index + 1}\t{x}\t{line_number * 20}\t10\t10\t90\t{value}"
            for index, (x, value) in enumerate(zip((0, 30, 90, 120), values))
        )

    mapped = map_tesseract_tsv(rows, "\n".join([header, *words]), layout)

    assert mapped[2, 1] == "1.2"
    assert mapped[3, 3] == "2.4"
    assert "99.0" not in mapped.values()


def test_tesseract_mapping_keeps_digit_suffix_row_labels_out_of_numeric_columns():
    labels = [
        "LiH", "BeH", "CH", "NH", "OH", "HF", "HCl", "Li2", "LiF",
        "CN", "CO", "N2", "NO", "O2", "F2", "Na2", "Si2", "P2", "S2",
        "Cl2", "NaCl", "SiO", "CS", "SO", "ClO", "ClF", "MAD",
    ]
    rows = [
        ["Dimer", "A", "B", "C", "D", "E", "F"],
        *[
            [label, *[f"{row}.{column}" for column in range(1, 7)]]
            for row, label in enumerate(labels, start=1)
        ],
    ]
    layout = _table_layout(rows, None)
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    words = []
    for line_number, row in enumerate(rows[1:], start=1):
        ocr_row = [re.sub(r"\d", "", row[0]), *row[1:]]
        positions = [
            60 + line_number % 4,
            330 + line_number % 7,
            555 + line_number % 5,
            800 + line_number % 9,
            1035 + line_number % 6,
            1260 + line_number % 8,
            1480 + line_number % 5,
        ]
        words.extend(
            f"5\t1\t1\t1\t{line_number}\t{index + 1}\t{x}\t{line_number * 20}\t20\t10\t90\t{text}"
            for index, (x, text) in enumerate(zip(positions, ocr_row))
        )

    mapped = map_tesseract_tsv(rows, "\n".join([header, *words]), layout)

    assert mapped[1, 1] == "1.1"
    assert mapped[1, 6] == "1.6"
    assert (1, 0) not in mapped


def test_tesseract_row_alignment_uses_text_labels_when_rows_are_missing():
    rows = [
        ["Model", "EN-DE", "EN-FR"],
        ["ByteNet [18]", "23.75", ""],
        ["Deep-Att + PosUnk [39]", "", "39.2"],
        ["GNMT + RL [38]", "24.6", "39.92"],
        ["ConvS2S [9]", "25.16", "40.46"],
        ["MoE [32]", "26.03", "40.56"],
        ["Deep-Att + PosUnk Ensemble [39]", "", "40.4"],
    ]
    layout = _table_layout(rows, None)
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    words = []
    for line_number, values in enumerate([
        [(0, "Deep-Att"), (80, "PosUnk"), (200, "39.2")],
        [(0, "GNMT"), (80, "RL"), (140, "24.6"), (200, "39.92")],
        [(0, "Deep-Att"), (80, "PosUnk"), (140, "Ensemble"), (200, "40.4")],
    ], start=1):
        words.extend(
            f"5\t1\t1\t1\t{line_number}\t{index + 1}\t{x}\t{line_number * 20}\t10\t10\t90\t{text}"
            for index, (x, text) in enumerate(values)
        )

    aligned = _aligned_tesseract_lines(rows, "\n".join([header, *words]), layout)

    assert [row for row, _ in aligned] == [2, 3, 6]


def test_tesseract_row_alignment_uses_numeric_row_keys_when_rows_are_missing():
    rows = [
        ["RADIUS", "VALUE"],
        ["0.130", "1.3"],
        ["0.180", "1.8"],
        ["0.550", "5.5"],
        ["0.800", "8.0"],
    ]
    layout = _table_layout(rows, None)
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    words = []
    for line_number, values in enumerate([
        ["0.l30", "1.3"],
        ["0.800", "8.0"],
    ], start=1):
        words.extend(
            f"5\t1\t1\t1\t{line_number}\t{index + 1}\t{x}\t{line_number * 20}\t10\t10\t90\t{value}"
            for index, (x, value) in enumerate(zip((0, 40), values))
        )

    aligned = _aligned_tesseract_lines(rows, "\n".join([header, *words]), layout)

    assert [row for row, _ in aligned] == [1, 4]


def test_tesseract_row_alignment_does_not_trust_equal_line_counts():
    rows = [
        ["ESO 3020120", "1.01", "0.40"],
        ["Galaxy", "rho", "chi"],
        ["ESO 0140040", "12.13", "25.28"],
        ["ESO 0840411", "0.25", "0.44"],
    ]
    layout = _table_layout(rows, None)
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    source_lines = [
        ["Galaxy", "rho", "chi"],
        ["ESO0140040", "12.13", "25.28"],
        ["ESO0840411", "0.25", "0.44"],
        ["ESO3020120", "1.01", "0.40"],
    ]
    words = []
    for line_number, values in enumerate(source_lines, start=1):
        words.extend(
            f"5\t1\t1\t1\t{line_number}\t{index + 1}\t{x}\t{line_number * 20}\t10\t10\t90\t{value}"
            for index, (x, value) in enumerate(zip((0, 100, 200), values))
        )

    aligned = _aligned_tesseract_lines(
        rows, "\n".join([header, *words]), layout
    )

    assert [(row, line[0]["text"]) for row, line in aligned] == [
        (2, "ESO0140040"),
        (3, "ESO0840411"),
    ]


def test_tesseract_row_alignment_uses_secondary_text_labels():
    rows = [
        ["", "", "8k", "16k", "32k", "65k"],
        ["LPIPS", "Hybrid GS", "0.36", "0.33", "0.31", "0.31"],
        ["LPIPS", "Vanilla GS", "0.56", "0.48", "0.42", "0.36"],
        ["MAE", "Hybrid GS", "6.41", "6.21", "5.92", "5.91"],
        ["MAE", "Vanilla GS", "7.27", "6.88", "6.66", "6.48"],
    ]
    layout = _table_layout(rows, None)
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    source_lines = [
        ["Hybrid", "GS", "0.36", "0.33", "0.31", "0.31"],
        ["Vanilla", "GS", "0.56", "0.48", "0.42", "0.36"],
        ["Hybrid", "GS", "641", "6.21", "5.92", "5.91"],
        ["Vanilla", "GS", "7.27", "6.88", "6.66", "6.48"],
    ]
    words = []
    for line_number, values in enumerate(source_lines, start=1):
        words.extend(
            f"5\t1\t1\t1\t{line_number}\t{index + 1}\t{x}\t{line_number * 20}\t10\t10\t90\t{value}"
            for index, (x, value) in enumerate(zip(range(0, 360, 60), values))
        )

    aligned = _aligned_tesseract_lines(
        rows, "\n".join([header, *words]), layout
    )

    assert [row for row, _ in aligned] == [1, 2, 3, 4]
    assert [line[2]["text"] for _, line in aligned] == [
        "0.36", "0.56", "641", "7.27"
    ]


def test_single_table_layout_is_not_carried_to_an_unrelated_table():
    first = [
        ["RADIUS", "A", ""],
        ["0.1", "1.0", ""],
    ]
    second = [
        ["Method", "Old", "New"],
        ["Model", "83.0", "77.9"],
    ]
    previous = _table_layout(first, None)

    current = _table_layout(second, previous)

    assert previous is not None and previous.columns == (("RADIUS", "A", ""),)
    assert current is not None and current.columns == (("", "numeric", "numeric"),)


def test_reader_grid_parse_gate_is_independent_of_engine_value():
    rows = [["1.0", "2.0"]]

    assert _reader_parse_rate(rows, {(0, 0): "9.0", (0, 1): "8.0"}) == 1.0
    assert _reader_parse_rate(rows, {(0, 0): "text", (0, 1): "?"}) == 0.0


def test_resolver_reports_local_continuity_without_rewriting_either_direction():
    rows = [
        ["0.1", "0.0005"],
        ["0.2", "0.0007"],
        ["0.3", "0.0009"],
    ]
    layout = _table_layout(rows, None)
    primary_record = _disagreement_record("0.0007", "9.0007")

    resolve_cell_records([primary_record], rows, layout)

    assert primary_record["best_value"] == "0.0007"
    assert primary_record["confidence"] == "low"
    assert primary_record["resolution_basis"] == "reader_disagreement_primary_retained"
    assert primary_record["validator_preference"] == "primary"
    assert primary_record["validator_basis"] == "local_continuity_primary"

    rows[1][1] = "9.0007"
    reader_record = _disagreement_record("9.0007", "0.0007")

    resolve_cell_records([reader_record], rows, layout)

    assert reader_record["best_value"] == "9.0007"
    assert reader_record["confidence"] == "low"
    assert reader_record["resolution_basis"] == "reader_disagreement_primary_retained"
    assert reader_record["validator_preference"] == "reader"
    assert reader_record["validator_basis"] == "local_continuity_reader"


def test_resolver_retains_primary_when_disagreement_is_ambiguous():
    record = _disagreement_record("2.0", "3.0", row=0)

    resolve_cell_records([record], [["1.0", "2.0"]], _table_layout([["1.0", "2.0"]], None))

    assert record["best_value"] == "2.0"
    assert record["confidence"] == "low"
    assert record["resolution_basis"] == "reader_disagreement_primary_retained"
    assert record["validator_preference"] is None
    assert record["validator_basis"] is None


def test_resolver_exposes_compact_numeric_value_for_grouped_digits():
    record = {
        "value": "-14.556 089",
        "numeric_value": "-14.556089",
        "value_status": "numeric",
        "external_reference_value": None,
        "external_outcome": "no_reference",
        "reader_outcome": "reader_refused",
        "source_row": 0,
        "source_column": 0,
    }

    resolve_cell_records([record], [["-14.556 089"]], None)

    assert record["primary_value"] == "-14.556089"
    assert record["best_value"] == "-14.556089"


def test_table_i_metadata_vertical_records_and_spacer_columns(tmp_path):
    doc, structure, table = _table_i_document()

    emit_document(doc, structure, tmp_path, {"title": "Doc"}, {"pdf2md": "test"})

    normalized = json.loads((tmp_path / table.normalized_json_path).read_text())
    assert normalized["representation"] == "table_i_records"
    assert [panel["metadata"]["atomic_number"] for panel in normalized["panels"]] == [44, 45]
    assert normalized["panels"][0]["metadata"] == {
        "atomic_number": 44,
        "symbol": "RU",
        "term": "5D",
        "configuration": "(4D)6",
    }
    with (tmp_path / table.normalized_data_path).open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert not any(row["column"] == "" for row in rows)
    assert rows[0]["raw_value"] == "1.2500"
    assert rows[0]["numeric_value"] == "1.2500"
    assert rows[0]["best_value"] == "1.2500"
    assert rows[0]["confidence"] == "low"
    assert rows[0]["verification_status"] == "candidate"
    assert rows[1]["value"] == "" and rows[1]["value_status"] == "dot_placeholder"


def test_table_i_interleaved_lanes_keep_scalar_values_with_their_element(tmp_path):
    block = Block("#/table", BlockType.TABLE, "", 1, extra={"ocr": True})
    grid = [
        ["2", "HE", "1S", "(1S)2", "", "", "", "", "",
         "10", "N", "1S", "(2P)6", "TOTAL ENERGY = -128.5474", "", "", "", ""],
        ["NL", "E", "", "A", "S", "R", "R**2", "1/R", "1/R**3",
         "NL", "E", "", "A", "S", "R", "R**2", "1/R", "1/R**3"],
        ["1S", "1.8", "", "4.7", "0.3", "0.9", "1.1", "1.6", "",
         "1S", "65.5", "", "60.7", "0.4", "0.1", "0.03", "9.6", ""],
        ["TOTAL ENERGY =", "", "-2.861685", "", "", "", "", "", "",
         "2S", "3.8", "", "14.2", "3.2", "0.8", "0.9", "1.6", ""],
        ["3", "LI", "2S", "(2S)", "", "", "", "", "",
         "2P", "1.7", "", "27.8", "4.8", "0.9", "1.2", "1.4", "10.9"],
        ["NL", "E", "", "A", "S", "R", "R**2", "1/R", "1/R**3",
         "", "", "", "", "", "", "", "", ""],
        ["1S", "4.9", "", "9.2", "0.3", "0.5", "0.4", "2.6", "",
         "F0 (2P, 2P) =", "", "0.968259", "", "", "F2 (2P, 2P) =", "", "0.427601", ""],
        ["TOTAL ENERGY =", "", "-7.432772", "", "", "", "", "", "",
         "11", "NA", "2S", "(3S)", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", "",
         "NL", "E", "", "A", "S", "R", "R**2", "1/R", "1/R**3"],
        ["", "", "", "", "", "", "", "", "",
         "1S", "80.9", "", "70.2", "0.5", "0.1", "0.02", "10.6", ""],
        ["", "", "", "", "", "", "", "", "",
         "TOTAL ENERGY =", "", "-161.8599", "", "", "", "", "", ""],
    ]
    lines = ["| " + " | ".join(row) + " |" for row in grid]
    lines.insert(1, "|" + "|".join(["---"] * 18) + "|")
    table = TableData(block.id, 1, None, "\n".join(lines))
    structure = build_structure([block], None, title="Doc", page_count=1)
    doc = Document(
        "a" * 64, "/source.pdf", "a" * 64, 1, 1, structure.root,
        blocks=[block], tables=[table],
    )

    emit_document(doc, structure, tmp_path, {"title": "Doc"}, {"pdf2md": "test"})

    normalized = json.loads((tmp_path / table.normalized_json_path).read_text())
    assert [panel["metadata"]["atomic_number"] for panel in normalized["panels"]] == [
        2, 3, 10, 11,
    ]
    with (tmp_path / table.normalized_data_path).open(newline="") as stream:
        records = list(csv.DictReader(stream))
    energies = {
        int(record["atomic_number"]): record["numeric_value"]
        for record in records
        if record["row_key"] == "TOTAL ENERGY ="
    }
    assert energies == {
        2: "-2.861685",
        3: "-7.432772",
        10: "-128.5474",
        11: "-161.8599",
    }
    assert all(
        record["column"] == "value"
        for record in records
        if record["row_key"].endswith("=")
    )
    neon_energy = next(
        record for record in records
        if record["atomic_number"] == "10" and record["row_key"] == "TOTAL ENERGY ="
    )
    assert neon_energy["symbol"] == "NE"
    assert neon_energy["raw_value"] == "TOTAL ENERGY = -128.5474"
    assert neon_energy["numeric_value"] == "-128.5474"
    assert neon_energy["primary_value"] == "-128.5474"
    assert neon_energy["best_value"] == "-128.5474"


def test_external_reference_evidence_and_gate(tmp_path):
    doc, structure, table = _table_i_document()
    reference = tmp_path / "reference.csv"
    reference.write_text(
        "atomic_number,row_key,column,value\n"
        "44,1S,E,1.2500\n"
        "45,1S,E,9.9999\n"
        "46,1S,E,2.0000\n"
    )

    emit_document(
        doc,
        structure,
        tmp_path,
        {"title": "Doc"},
        {"pdf2md": "test"},
        table_reference_path=str(reference),
    )

    evidence = [
        json.loads(line)
        for line in (tmp_path / table.cell_evidence_path).read_text().splitlines()
    ]
    statuses = {
        (item["semantic_key"] or {}).get("atomic_number"): item["verification_status"]
        for item in evidence
        if (item["semantic_key"] or {}).get("column") == "E"
    }
    assert statuses == {"44": "externally_verified", "45": "external_disagreement"}
    decisions = {
        (item["semantic_key"] or {}).get("atomic_number"): (
            item["best_value"], item["confidence"], item["resolution_basis"]
        )
        for item in evidence
        if (item["semantic_key"] or {}).get("column") == "E"
    }
    assert decisions == {
        "44": ("1.2500", "verified", "external_reference_agreement"),
        "45": ("9.9999", "verified", "external_reference_override"),
    }
    with (tmp_path / table.normalized_data_path).open(newline="") as stream:
        normalized = list(csv.DictReader(stream))
    assert {
        row["atomic_number"]: row["best_value"]
        for row in normalized if row["column"] == "E"
    } == {"44": "1.2500", "45": "9.9999"}

    report = compare_external_reference(tmp_path, reference)
    assert (report["agree"], report["disagree"], report["tool_refused"]) == (1, 1, 1)
    assert compare_external_reference(tmp_path, None)["no_reference"] == 1
