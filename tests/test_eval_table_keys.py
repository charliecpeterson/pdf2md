"""Two-pass row-key evidence accepts agreement and preserves OCR ambiguity."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from PIL import Image


SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "eval_table_keys", SCRIPTS / "eval_table_keys.py"
)
keys = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(keys)
finally:
    sys.path.pop(0)


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


def test_label_words_stop_before_numeric_cells():
    line = [_word("HCl", 10), _word("-0.22", 100), _word("-0.01", 180)]

    assert [word["text"] for word in keys._label_words(line)] == ["HCl"]


def test_consensus_keeps_case_and_subscript_variants_but_not_i_l_confusion():
    assert keys._consensus("Cl₂", "cl2") == "Cl₂"
    assert keys._consensus("HCI", "HCl") is None
    assert keys._consensus("CIF", "CF") is None


def test_verification_refuses_shared_confusable_glyph_error():
    assert keys._verified_key("HCI", "HCI", "HCI") == (
        None,
        "confusable_glyph",
    )
    assert keys._verified_key("HCl", "HCl", "HCl") == (
        None,
        "confusable_glyph",
    )
    assert keys._verified_key("Na2", "na2", "Na₂") == (
        "Na2",
        "three_way_agreement",
    )


def test_verification_requires_primary_and_both_reads():
    assert keys._verified_key("CIO", "clo", "clo") == (
        None,
        "primary_reader_disagreement",
    )
    assert keys._verified_key("SO", "SO", None) == (None, "reader_missing")


def test_page_words_keeps_multipage_tiff_reads_separate():
    header = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
        "left\ttop\twidth\theight\tconf\ttext"
    )
    tsv = "\n".join([
        header,
        "5\t1\t1\t1\t1\t1\t20\t0\t10\t10\t90\tCl",
        "5\t1\t1\t1\t1\t2\t40\t0\t10\t10\t90\tF",
        "5\t2\t1\t1\t1\t1\t10\t0\t10\t10\t90\tHCl",
    ])

    assert keys._page_words(tsv) == {1: "Cl F", 2: "HCl"}


def test_service_crop_adds_canvas_without_rescaling_glyphs():
    crop = Image.new("L", (120, 80), 128)

    padded = keys._service_crop(crop)

    assert padded.mode == "RGB"
    assert padded.size == (640, 192)
    assert padded.crop((260, 56, 380, 136)).getbbox() == (0, 0, 120, 80)


def test_paddle_read_requires_one_nonempty_text_block():
    response = {
        "errorCode": 0,
        "result": {"layoutParsingResults": [{"prunedResult": {
            "parsing_res_list": [{"block_content": "ClO"}],
        }}]},
    }

    assert keys._paddle_read(response) == ("ClO", None)
    response["result"]["layoutParsingResults"][0]["prunedResult"][
        "parsing_res_list"
    ] = []
    assert keys._paddle_read(response) == (None, "paddle_text_missing")


def test_paddle_run_preserves_missing_text_as_refusal(tmp_path):
    crop = tmp_path / "key.png"
    crop.write_bytes(b"crop")
    response = tmp_path / "key.paddle.json"
    response.write_text(json.dumps({"errorCode": 0, "result": {
        "layoutParsingResults": [{"prunedResult": {"parsing_res_list": []}}]
    }}))
    run = tmp_path / "paddle-run.json"
    run.write_text(json.dumps({"results": [{
        "variant": "hard",
        "source_row": 2,
        "input": crop.name,
        "input_sha256": keys._sha256(crop),
        "response": response.name,
        "response_sha256": keys._sha256(response),
        "http_status": 200,
        "error_code": 0,
    }]}))
    report = {"records": [{
        "variant": "hard",
        "source_row": 2,
        "expected": "ClO",
        "verified_outcome": "tool_refused",
    }]}

    keys.add_paddle_results(report, run)

    assert report["paddle"] == {
        "reader": "PaddleOCR-VL 1.6 layout-parsing service",
        "run": str(run),
        "tool": {},
        "routed": 1,
        "agree": 0,
        "disagree": 0,
        "tool_refused": 1,
        "refusal_reasons": {"paddle_text_missing": 1},
    }
