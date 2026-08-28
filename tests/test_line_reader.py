"""The optional line reader stays hash-pinned and cannot rewrite table values."""

from __future__ import annotations

import json

import pytest

from pdf2md import line_reader
from pdf2md.table_verify import _table_layout
from pdf2md.tables import RepeatedPanelLayout


def _word(text: str, left: int) -> dict[str, object]:
    return {
        "text": text,
        "left": left,
        "top": 10,
        "width": 20,
        "height": 10,
        "x": left + 10,
        "y": 15,
    }


def test_row_key_words_handle_text_and_numeric_keys():
    line = [_word("3p-", 10), _word("2.81D+02", 100), _word("1", 180)]
    assert [word["text"] for word in line_reader._row_key_words("3p-", line)] == ["3p-"]

    numeric = [_word("0.0008", 10), _word("-0.42", 100)]
    assert [word["text"] for word in line_reader._row_key_words("0.0008", numeric)] == [
        "0.0008"
    ]
    spaced = [_word("Cl", 10), _word("17", 40), _word("-0.2", 100)]
    assert [word["text"] for word in line_reader._row_key_words("Cl 17", spaced)] == [
        "Cl", "17",
    ]


def test_key_normalization_preserves_semantic_punctuation():
    assert line_reader._key("Cl₂") == line_reader._key("cl2")
    assert line_reader._key("3p−") == line_reader._key("3p-")
    assert line_reader._key("3p-") != line_reader._key("3p")
    assert line_reader._key("0.0008") != line_reader._key("00008")


def test_panel_key_bounds_require_a_distinct_inter_panel_gap():
    layout = RepeatedPanelLayout(
        starts=(0, 3),
        width=3,
        titles=("left", "right"),
        columns=(("RADIUS", "A", "B"), ("RADIUS", "A", "B")),
    )
    centers = [(0, 50.0), (1, 100.0), (2, 150.0),
               (3, 300.0), (4, 350.0), (5, 400.0)]

    assert line_reader._panel_key_bounds(layout, centers, 450) == {
        0: (25.0, 75.0),
        3: (275.0, 325.0),
    }

    unseparated = [(column, 50.0 + column * 50.0) for column in range(6)]
    assert line_reader._panel_key_bounds(layout, unseparated, 350) is None

    single = RepeatedPanelLayout(
        starts=(0,), width=3, titles=("",), columns=(("RADIUS", "A", "B"),)
    )
    assert line_reader._panel_key_bounds(single, centers[:3], 200) == {
        0: (25.0, 75.0)
    }


def test_words_in_bounds_keeps_only_one_panel_key():
    line = [_word("0.0008", 20), _word("1.2", 100), _word("0.0008", 300)]

    assert [word["text"] for word in line_reader._words_in_bounds(line, (0, 80))] == [
        "0.0008"
    ]


def test_panel_key_alignment_requires_counts_and_matching_y_positions(monkeypatch):
    layout = RepeatedPanelLayout(
        starts=(0, 2),
        width=2,
        titles=("left", "right"),
        columns=(("RADIUS", "A"), ("RADIUS", "A")),
    )
    rows = [["0.1", "1", "0.1", "2"], ["0.2", "3", "0.2", "4"]]
    lines = [
        [_word("0.1", 20)], [_word("0.1", 220)],
        [{**_word("0.2", 20), "y": 35}],
        [{**_word("0.2", 220), "y": 36}],
    ]
    monkeypatch.setattr(line_reader, "_word_lines", lambda _tsv: lines)

    aligned = line_reader._aligned_panel_key_words(
        rows, [0, 1], layout, "tsv", {0: (0, 80), 2: (200, 280)}
    )

    assert aligned is not None
    assert [word["text"] for word in aligned[1, 2]] == ["0.2"]

    monkeypatch.setattr(line_reader, "_word_lines", lambda _tsv: lines[:-1])
    partial = line_reader._aligned_panel_key_words(
        rows, [0, 1], layout, "tsv", {0: (0, 80), 2: (200, 280)}
    )
    assert partial is not None
    assert (0, 2) in partial
    assert (1, 2) not in partial

    monkeypatch.setattr(line_reader, "_word_lines", lambda _tsv: lines)
    lines[-1][0]["y"] = 60
    misaligned = line_reader._aligned_panel_key_words(
        rows, [0, 1], layout, "tsv", {0: (0, 80), 2: (200, 280)}
    )
    assert misaligned is not None
    assert (1, 2) not in misaligned

    monkeypatch.setattr(
        line_reader, "_word_lines", lambda _tsv: [lines[0], lines[1]]
    )
    assert line_reader._aligned_panel_key_words(
        rows, [0, 1], layout, "tsv", {0: (0, 80), 2: (200, 280)}
    ) is None


def test_localized_panel_alignment_keeps_lanes_independent_and_monotonic(monkeypatch):
    layout = RepeatedPanelLayout(
        starts=(0, 2),
        width=2,
        titles=("left", "right"),
        columns=(("RADIUS", "A"), ("RADIUS", "A")),
    )
    rows = [
        ["0.1", "1", "0.1", "2"],
        ["0.2", "3", "", ""],
        ["", "", "0.2", "4"],
        ["0.15", "5", "0.3", "6"],
    ]
    lines = [
        [_word("0.1", 20)],
        [_word("0.1", 220)],
        [{**_word("0.2", 20), "y": 35}],
        [{**_word("0.2", 220), "y": 35}],
        [{**_word("0.15", 20), "y": 55}],
        [{**_word("0.3", 220), "y": 55}],
    ]
    monkeypatch.setattr(line_reader, "_word_lines", lambda _tsv: lines)
    source_rows = line_reader._panel_numeric_source_rows(rows, layout)

    aligned, refusals = line_reader._localized_panel_key_words(
        rows, source_rows, layout, "tsv", {0: (0, 80), 2: (200, 280)}
    )

    assert source_rows == {0: [0, 1, 3], 2: [0, 2, 3]}
    assert set(aligned) == {(0, 0), (1, 0), (0, 2), (2, 2), (3, 2)}
    assert refusals[3, 0] == "panel_key_not_increasing"


def test_line_reader_rows_follow_numeric_panel_keys_not_row_density():
    layout = RepeatedPanelLayout(
        starts=(0, 3),
        width=3,
        titles=("left", "right"),
        columns=(("RADIUS", "A", "B"), ("RADIUS", "A", "B")),
    )
    rows = [
        ["ATOMIC NUMBER 8", "", "", "ATOMIC NUMBER 9", "", ""],
        ["RADIUS", "A", "B", "RADIUS", "A", "B"],
        ["0.0001", ".", "", "0.0001", ".", ""],
        ["0.0002", "", ".", "0.0002", "", "."],
    ]

    assert line_reader._line_reader_source_rows(rows, layout) == [2, 3]


def test_repeated_layout_carries_into_a_headerless_continuation():
    header = [
        ["RADIUS", "1S", "RADIUS", "1S"],
        ["0.0001", "0.1", "0.0001", "0.2"],
    ]
    continuation = [
        ["0.180", "0.3", "0.180", "0.4"],
        ["0.200", "0.5", "0.200", "0.6"],
    ]
    previous = _table_layout(header, None)
    direct = _table_layout(continuation, None)
    carried = _table_layout(continuation, previous)

    assert previous is not None
    assert direct is not None and len(direct.starts) == 1
    assert carried is not None and carried.starts == (0, 2)
    assert line_reader._layout_family(carried, inherited=True) == (
        "repeated_2_panel_continuation"
    )


def test_apply_accepts_only_high_score_primary_agreement(tmp_path):
    version = tmp_path / "doc" / "v1"
    version.mkdir(parents=True)
    provenance = version / "provenance.json"
    provenance.write_text("{}")
    output = tmp_path / "reader"
    crops = output / "crops"
    crops.mkdir(parents=True)
    source_crops = output / "source-crops"
    source_crops.mkdir()
    source_crop = source_crops / "table.png"
    source_crop.write_bytes(b"source crop")
    for name in ("agree.png", "wrong.png", "low.png"):
        (crops / name).write_bytes(name.encode())
    records = [
        {
            "id": name,
            "source_block_id": "#/table/1",
            "primary_value": primary,
            "source_crop": "source-crops/table.png",
            "source_crop_sha256": line_reader._sha256(source_crop),
            "crop": f"crops/{name}.png",
            "crop_sha256": line_reader._sha256(crops / f"{name}.png"),
        }
        for name, primary in (("agree", "3p-"), ("wrong", "ClF"), ("low", "6s"))
    ]
    manifest = {
        "schema_version": 1,
        "method": "test",
        "version_dir": str(version),
        "version_provenance_sha256": line_reader._sha256(provenance),
        "source_sha256": "a" * 64,
        "records": records,
        "refusals": [{"reason": "single_panel_required"}],
        "tables": [{
            "source_block_id": "#/table/1",
            "page": 1,
            "layout_family": "single_panel",
            "panel_count": 1,
            "panel_widths": [2],
            "source_rows": 3,
            "expected_key_cells": 3,
            "prepared": 3,
            "unprepared_key_cells": 0,
            "preparation_refused": 1,
            "refusal_reasons": {"single_panel_required": 1},
        }],
    }
    (output / "manifest.json").write_text(json.dumps(manifest))
    run = tmp_path / "run.json"
    run.write_text(json.dumps({
        "reader": line_reader.PINNED_READER,
        "records": [
            {
                "id": "agree", "input_sha256": records[0]["crop_sha256"],
                "text": "3P-", "score": 0.999, "error": None,
            },
            {
                "id": "wrong", "input_sha256": records[1]["crop_sha256"],
                "text": "CIF", "score": 0.999, "error": None,
            },
            {
                "id": "low", "input_sha256": records[2]["crop_sha256"],
                "text": "6s", "score": 0.98, "error": None,
            },
        ],
    }))

    report = line_reader.apply(output, run)

    assert report["reader_agreement"] == 1
    assert report["reader_refused"] == 2
    assert report["preparation_refused"] == 1
    assert report["expected_key_cells"] == 3
    assert report["unprepared_key_cells"] == 0
    assert report["preparation_refusal_events"] == 1
    assert report["locator_methods"] == {"legacy": 3}
    assert report["layout_families"]["single_panel"] == {
        "tables": 1,
        "expected_key_cells": 3,
        "prepared": 3,
        "unprepared_key_cells": 0,
        "preparation_refused": 1,
        "reader_agreement": 1,
        "reader_refused": 2,
        "preparation_refusal_reasons": {"single_panel_required": 1},
        "locator_methods": {},
    }
    evidence = [json.loads(line) for line in (output / "evidence.jsonl").read_text().splitlines()]
    assert [record["verification_status"] for record in evidence] == [
        "reader_agreement", "reader_refused", "reader_refused",
    ]
    assert evidence[1]["reader_refusal_reason"] == "reader_primary_disagreement"
    assert evidence[2]["reader_refusal_reason"] == "reader_score_below_threshold"
    assert all(record["primary_value"] in {"3p-", "ClF", "6s"} for record in evidence)


def test_apply_rejects_an_unbenchmarked_reader(tmp_path):
    output = tmp_path / "reader"
    output.mkdir()
    version = tmp_path / "v1"
    version.mkdir()
    provenance = version / "provenance.json"
    provenance.write_text("{}")
    (output / "manifest.json").write_text(json.dumps({
        "method": "test",
        "version_dir": str(version),
        "version_provenance_sha256": line_reader._sha256(provenance),
        "source_sha256": "a" * 64,
        "records": [],
        "refusals": [],
    }))
    run = tmp_path / "run.json"
    run.write_text(json.dumps({
        "reader": {**line_reader.PINNED_READER, "model_name": "other"},
        "records": [],
    }))

    with pytest.raises(ValueError, match="unapproved reader identity: model_name"):
        line_reader.apply(output, run)


def test_prepare_rejects_reversed_page_range(tmp_path):
    with pytest.raises(ValueError, match="page_from must not exceed page_to"):
        line_reader.prepare(tmp_path, tmp_path / "reader", page_from=8, page_to=7)


def test_prepare_records_and_applies_inclusive_page_range(tmp_path, monkeypatch):
    document = tmp_path / "doc"
    version = document / "v1"
    version.mkdir(parents=True)
    source = document / "source.pdf"
    source.write_bytes(b"source")
    tables = [
        {
            "block_id": f"#/table/{page}",
            "page": page,
            "gfm": "| RADIUS | A |\n|---|---|\n| 0.1 | 1.0 |",
            "bbox": {"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        }
        for page in (1, 2, 3)
    ]
    (version / "provenance.json").write_text(json.dumps({
        "source_sha256": line_reader._sha256(source),
        "blocks": [
            {"id": table["block_id"], "bbox": table["bbox"]}
            for table in tables
        ],
        "tables": tables,
    }))

    monkeypatch.setattr(line_reader.shutil, "which", lambda _name: "tesseract")
    monkeypatch.setattr(line_reader, "_run_tesseract", lambda *_args: "")

    def write_crop(_source, _table, _block, crop_path):
        line_reader.Image.new("RGB", (100, 100), "white").save(crop_path)

    monkeypatch.setattr(line_reader, "_table_crop", write_crop)

    manifest = line_reader.prepare(
        version, tmp_path / "reader", page_from=2, page_to=2
    )

    assert manifest["selection"] == {
        "block_ids": None,
        "page_from": 2,
        "page_to": 2,
    }
    assert [table["page"] for table in manifest["tables"]] == [2]
    assert manifest["expected_key_cells"] == 1
    assert manifest["unprepared_key_cells"] == 1
    assert manifest["refusals"] == [{
        "source_block_id": "#/table/2",
        "source_row": 1,
        "source_column": 0,
        "panel": 0,
        "primary_value": "0.1",
        "reason": "row_key_alignment_missing",
    }]
