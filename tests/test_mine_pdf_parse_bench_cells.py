"""The benchmark miner maps only scalar numeric cells in nearly matching rows."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "mine_pdf_parse_bench_cells", SCRIPTS / "mine_pdf_parse_bench_cells.py"
)
miner = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(miner)
finally:
    sys.path.pop(0)


def test_latex_scalar_rows_unwrap_formatting_without_counting_headers():
    latex = r"""
        \multirow{2}[3]{*}{Method} & \multicolumn{3}{c}{ImageNet-100} \\
        SimGCD & 83.0 & \textbf{93.1} & \hphantom{00}77.9 \\
        Ours & \color{gray}{87.1} & \textbf{3.48}7 & 68.9 \\
    """

    assert miner._latex_scalar_rows(latex) == [
        [(1, "83"), (2, "93.1"), (3, "77.9")],
        [(1, "87.1"), (2, "3.487"), (3, "68.9")],
    ]


def test_row_mapping_requires_all_but_at_most_one_scalar_to_match():
    ground = [
        [(1, "83"), (2, "93.1"), (3, "77.9")],
        [(1, "87.1"), (2, "93.9"), (3, "83.7")],
    ]
    extracted = [
        (2, [(1, "83"), (2, "93.1"), (3, "77.9")]),
        (5, [(1, "87.1"), (2, "33.9"), (3, "83.7")]),
        (8, [(1, "1"), (2, "2"), (3, "3")]),
    ]

    assert miner._row_matches(ground, extracted) == [(0, 0, 3), (1, 1, 2)]


def test_ground_truth_index_recovers_copied_source_name(tmp_path):
    ground_truth = tmp_path / "ground_truth"
    pdfs = tmp_path / "pdfs"
    ground_truth.mkdir()
    pdfs.mkdir()
    source = pdfs / "077.pdf"
    source.write_bytes(b"source bytes")
    label = ground_truth / "077.json"
    label.write_text("[]")

    index = miner._ground_truth_by_source_sha(ground_truth)

    assert index[miner._sha256(source)] == label


def test_report_labels_keep_source_coordinates_and_ground_truth_pin():
    report = {
        "documents": [{
            "document_id": "019",
            "source": "/corpus/019.pdf",
            "source_sha256": "source-hash",
            "version": "v1",
            "ground_truth": "/corpus/019.json",
            "ground_truth_sha256": "ground-hash",
        }],
        "cells": [{
            "document_id": "019",
            "page": 1,
            "block_id": "#/tables/2",
            "source_row": 4,
            "source_column": 3,
            "expected": "0.125",
            "ground_table": 2,
            "ground_row": 3,
            "position": 1,
        }],
    }

    assert miner.labels_from_report(report) == {
        "schema_version": 1,
        "method": "pdf_parse_bench_scalar_cells",
        "documents": [{
            "source_sha256": "source-hash",
            "source": "/corpus/019.pdf",
            "version": "v1",
            "ground_truth": "/corpus/019.json",
            "ground_truth_sha256": "ground-hash",
            "cells": [{
                "page": 1,
                "block_id": "#/tables/2",
                "row": 4,
                "column": 3,
                "expected": "0.125",
                "label": "pdf-parse-bench 019 table 2 row 3 scalar 1",
            }],
        }],
    }
