"""Cell-crop geometry must isolate the labeled table value."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
try:
    from prepare_paddleocr_crops import (
        _crop_aligned_lines,
        _exact_numeric_cell_bounds,
        _limit_labels,
        _numeric_word_y_bounds,
        _reader_disagreement_labels,
        _text_label_cell_bounds,
        write_source_box_crops,
    )
finally:
    sys.path.pop(0)

from pdf2md.table_verify import _table_layout


def test_text_label_cell_bounds_excludes_adjacent_numeric_column():
    row = ["GNMT + RL [38]", "24.6", "39.92", "2 . 3 · 10 19", "1 . 4 · 10 20"]
    words = [
        ("GNMT", 60, 131),
        ("+", 205, 23),
        ("RL", 241, 56),
        ("[38]", 313, 68),
        ("24.6", 779, 76),
        ("39.92", 956, 98),
        ("2.3-10!9", 1181, 168),
        ("1.4-", 1419, 73),
        ("107°", 1510, 75),
    ]
    line = [
        {"text": text, "left": left, "width": width, "height": 38}
        for text, left, width in words
    ]

    assert _text_label_cell_bounds(row, line, 2, 1600) == (953, 1057)


def test_text_label_cell_bounds_does_not_join_across_vertical_rule():
    row = ["3s2p1d", "-2 900.232", "3.49", "-2 901.840", "1.88", "86", "33"]
    words = [
        ("3s2pld", 41, 90, 27),
        ("-2", 227, 23, 21),
        ("900.232", 263, 99, 22),
        ("3.49", 527, 52, 22),
        ("|", 593, 14, 37),
        ("-2", 619, 24, 21),
        ("901.840", 655, 99, 22),
        ("1.88", 942, 51, 22),
        ("86", 1276, 28, 22),
        ("33", 1568, 28, 22),
    ]
    line = [
        {"text": text, "left": left, "width": width, "height": height}
        for text, left, width, height in words
    ]

    assert _text_label_cell_bounds(row, line, 3, 1700) == (616, 757)


def test_exact_numeric_cell_bounds_uses_unique_matching_word():
    row = ["ByteNet [18]", "23.75", "", "", ""]
    line = [
        {"text": "ByteNet", "left": 59, "width": 150},
        {"text": "[18]", "left": 225, "width": 67},
        {"text": "23.75", "left": 768, "width": 97},
    ]

    assert _exact_numeric_cell_bounds(row, line, 1, 1654) == (765, 868)


def test_numeric_word_y_bounds_excludes_fragments_from_adjacent_row():
    line = [
        {"text": "|", "left": 740, "top": 353, "width": 8, "height": 69},
        {"text": "81.3", "left": 866, "top": 389, "width": 143, "height": 51},
    ]

    assert _numeric_word_y_bounds(line, (863, 1012), 1400) == (388, 441)


def test_crop_alignment_can_include_requested_sparse_rows():
    rows = [
        ["Model", "EN-DE", "EN-FR", "Cost EN-DE", "Cost EN-FR"],
        ["ByteNet [18]", "23.75", "", "", ""],
        ["Deep-Att [39]", "", "39.2", "", "1.0e20"],
    ]
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    words = [
        "5\t1\t1\t1\t1\t1\t0\t20\t10\t10\t90\tByteNet",
        "5\t1\t1\t1\t1\t2\t100\t20\t10\t10\t90\t23.75",
        "5\t1\t1\t1\t2\t1\t0\t40\t10\t10\t90\tDeep-Att",
        "5\t1\t1\t1\t2\t2\t200\t40\t10\t10\t90\t39.2",
        "5\t1\t1\t1\t2\t3\t400\t40\t10\t10\t90\t1.0e20",
    ]

    aligned = _crop_aligned_lines(
        rows,
        "\n".join([header, *words]),
        _table_layout(rows, None),
        {1, 2},
    )

    assert [row for row, _ in aligned] == [1, 2]


def test_reader_disagreement_labels_keep_only_comparable_disagreements():
    labels = {"schema_version": 1, "documents": [{
        "source_sha256": "a" * 64,
        "version": "v1",
        "cells": [
            {"page": 1, "block_id": "#/table", "row": 0, "column": 1, "expected": "1"},
            {"page": 1, "block_id": "#/table", "row": 0, "column": 2, "expected": "2"},
        ],
    }]}
    report = {"records": [
        {
            "source_sha256": "a" * 64,
            "block_id": "#/table",
            "row": 0,
            "column": 1,
            "actual": "1",
            "reference_actual": "7",
            "readers_agree": False,
        },
        {
            "source_sha256": "a" * 64,
            "block_id": "#/table",
            "row": 0,
            "column": 2,
            "actual": "2",
            "reference_actual": None,
            "readers_agree": False,
        },
    ]}

    filtered = _reader_disagreement_labels(labels, report)

    assert filtered["documents"][0]["version"] == "v1"
    assert filtered["documents"][0]["cells"] == [labels["documents"][0]["cells"][0]]


def test_label_limit_round_robins_across_tables():
    labels = {"schema_version": 1, "documents": [{
        "source_sha256": "a" * 64,
        "cells": [
            {"block_id": "#/one", "row": 0, "column": 0},
            {"block_id": "#/one", "row": 1, "column": 0},
            {"block_id": "#/two", "row": 0, "column": 0},
            {"block_id": "#/two", "row": 1, "column": 0},
        ],
    }]}

    limited = _limit_labels(labels, 3)

    assert limited["documents"][0]["cells"] == [
        labels["documents"][0]["cells"][0],
        labels["documents"][0]["cells"][1],
        labels["documents"][0]["cells"][2],
    ]


def test_source_box_crops_use_pinned_geometry_without_tesseract(tmp_path):
    source_hash = "a" * 64
    version = tmp_path / "outputs" / source_hash[:16] / "v1"
    crop_path = version / "assets" / "table.png"
    crop_path.parent.mkdir(parents=True)
    Image.new("L", (20, 10), 255).save(crop_path)
    (version / "manifest.json").write_text(json.dumps({
        "representations": {"tables": [{
            "block_id": "#/table",
            "crop": "assets/table.png",
        }]},
    }))
    (version / "provenance.json").write_text("{}")
    labels = {"documents": [{
        "source_sha256": source_hash,
        "version": "v1",
        "cells": [{
            "page": 1,
            "block_id": "#/table",
            "row": 0,
            "column": 0,
            "expected": "1.0",
        }],
    }]}
    source_boxes = {"schema_version": 1, "documents": [{
        "source_sha256": source_hash,
        "cells": [{
            "block_id": "#/table",
            "row": 0,
            "column": 0,
            "source_crop_sha256": hashlib.sha256(crop_path.read_bytes()).hexdigest(),
            "source_box": [2, 1, 7, 6],
        }],
    }]}

    manifest = write_source_box_crops(
        tmp_path / "outputs", labels, source_boxes, tmp_path / "prepared", 5
    )

    assert manifest["method"] == "human_verified_source_pixel_boxes"
    assert manifest["crops"][0]["source_box"] == [2, 1, 7, 6]
    prepared_crop = tmp_path / "prepared" / manifest["crops"][0]["path"]
    assert Image.open(prepared_crop).size == (25, 25)
    inputs = json.loads((tmp_path / "prepared" / "inputs.json").read_text())
    assert inputs["records"][0]["id"] == manifest["crops"][0]["id"]


def test_source_box_crops_require_exact_label_coverage(tmp_path):
    labels = {"documents": [{
        "source_sha256": "a" * 64,
        "cells": [{"block_id": "#/table", "row": 0, "column": 0}],
    }]}

    with pytest.raises(ValueError, match="source boxes and labels diverged"):
        write_source_box_crops(
            tmp_path, labels, {"schema_version": 1, "documents": []}, tmp_path / "out", 5
        )
