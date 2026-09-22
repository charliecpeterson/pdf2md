"""Synthetic negative controls, separate from naturally occurring scan omissions."""

import importlib.util
import json
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("probe_scan_omissions", SCRIPTS / "probe_scan_omissions.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
from _scan_omissions import (  # noqa: E402 - dev helper is loaded from scripts, not installed
    compare_lines,
    normalized,
    read_lines,
    substring_distance,
)

BOX = [10, 50, 90, 60]
TEXT = "The sample contains carbon from plant material."


def line(text=TEXT, box=None, confidence=95):
    box = box or BOX
    return {"text": text, "bbox": box, "words": [{"text": text, "bbox": box}],
            "mean_reader_confidence": confidence, "line_id": "1:1:1"}


def block(text=TEXT, box=None, **kwargs):
    return {"id": "p", "page": 1, "type": "paragraph", "coverage_status": "emitted",
            "bbox": box or [0, 0, 100, 100], "text": text, **kwargs}


@pytest.mark.parametrize(("needle", "text", "distance"), [
    ("carbon", "prefixcarbonsuffix", 0), ("carbon", "c a r b o n", 5),
    ("carbon", "carbn", 1), ("carbon", "carbzon", 1), ("carbon", "cqrbon", 1),
    ("carbon", "", 6), ("", "carbon", 0),
])
def test_substring_distance(needle, text, distance):
    assert substring_distance(needle, text) == distance


def test_retained_line_inside_paragraph_is_not_a_candidate():
    result, = compare_lines([line()], [block("Leading sentence. " + TEXT + " More text.")])
    assert (result["status"], result["distance"], result["block_ids"]) == ("reader_agreement", 0, ["p"])


def test_missing_line_inside_a_large_paragraph_box_is_a_candidate():
    result, = compare_lines([line()], [block("The sample is described here.")])
    assert result["status"] == "reader_disagreement"
    assert result["distance"] > 0.2


def test_another_column_does_not_cover_missing_text():
    result, = compare_lines([line()], [block(box=[110, 0, 200, 100])])
    assert (result["status"], result["distance"], result["block_ids"]) == (
        "reader_disagreement", 1, [],
    )


def test_spacing_and_script_markup_do_not_manufacture_disagreement():
    source = line("Carbon 12 is concentrated in plant material.")
    output = block("Carbon<sup>12</sup>isconcentratedinplant material.")
    result, = compare_lines([source], [output])
    assert (result["status"], result["distance"]) == ("reader_agreement", 0)
    assert normalized("ＣＡＲＢＯＮ &amp; 12") == "carbon12"


def test_crop_authority_requires_complete_word_coverage():
    crop = block("", type="table", coverage_status="cropped", content_crop=True)
    result, = compare_lines([line()], [crop])
    assert (result["status"], result["block_ids"]) == ("content_crop", ["p"])
    crop["bbox"] = [0, 0, 89, 100]
    result, = compare_lines([line()], [crop])
    assert (result["status"], result["distance"]) == ("reader_disagreement", 1)


def test_page_evidence_and_link_only_crops_do_not_establish_content(tmp_path):
    for name in ["page_001.png", "table.png"]:
        (tmp_path / name).write_bytes(b"test")
    b = block(extra={"crop_path": "page_001.png"})
    assert probe.content_crop(b, tmp_path, "![page](page_001.png)") is False
    b["extra"]["crop_path"] = "table.png"
    assert probe.content_crop(b, tmp_path, "[audit image](table.png)") is False
    assert probe.content_crop(b, tmp_path, "![table](table.png)") is True
    b["extra"]["crop_path"] = "absent.png"
    assert probe.content_crop(b, tmp_path, "![table](absent.png)") is False


@pytest.mark.parametrize(("source", "expected"), [
    (line(confidence=69), "low_reader_confidence"), (line("Page 1"), "short_line"),
])
def test_unassessed_lines_are_not_declared_clean(source, expected):
    result, = compare_lines([source], [])
    assert (result["status"], result["distance"]) == (expected, None)


def test_furniture_and_unlocated_regions_remain_explicit():
    result, = compare_lines([line()], [block(type="page_header")])
    assert result["status"] == "declared_furniture"
    b = block()
    b["bbox"] = None
    result, = compare_lines([line()], [b])
    assert result["status"] == "unlocated_representation"
    assert compare_lines([], [block()]) == []


HEADER = "level\tblock_num\tpar_num\tline_num\tleft\ttop\twidth\theight\tconf\ttext\n"


def test_reader_coordinates_use_actual_raster_size_and_page_origin():
    rows = read_lines(HEADER + "5\t1\t1\t1\t20\t40\t100\t20\t90\tword\n",
                      200, 400, (10, 30, 110, 230))
    assert len(rows) == 1
    assert rows[0]["bbox"] == [20, 200, 70, 210]
    assert rows[0]["mean_reader_confidence"] == 90
    assert read_lines(HEADER, 200, 400, (10, 30, 110, 230)) == []


@pytest.mark.parametrize("tsv", [
    "garbage", HEADER + "5\t1\t1\t1\t20\t40\t100\t20\tnan\tword\n",
    HEADER + "5\t1\t1\t1\t-20\t40\t100\t20\t90\tword\n",
])
def test_invalid_reader_data_is_refused(tsv):
    with pytest.raises(ValueError):
        read_lines(tsv, 200, 400, (0, 0, 100, 200))


def test_probe_refuses_writing_inside_or_over_existing_inputs(tmp_path):
    bundle = tmp_path / "doc" / "v1"
    bundle.mkdir(parents=True)
    for output in [bundle / "probe", tmp_path / "doc" / "v2", tmp_path]:
        with pytest.raises(ValueError, match="new output directory"):
            probe.scan(bundle, output, tmp_path)


@pytest.mark.parametrize(("reader_result", "expected"), [
    (HEADER, "no_reader_lines"), ("garbage", "invalid_reader_output"),
    ("failure", "reader_failed"), ("timeout", "reader_timeout"),
    (HEADER + f"5\t1\t1\t1\t20\t40\t100\t20\t95\t{TEXT}\n", "compared"),
])
def test_runner_records_reader_failures_and_rotation_without_mutating_bundle(
    tmp_path, monkeypatch, reader_result, expected,
):
    bundle = tmp_path / "document" / "v1"
    bundle.mkdir(parents=True)
    source = bundle.parent / "source.pdf"
    with closing(probe.pdfium.PdfDocument.new()) as pdf:
        for rotation in [0, 90]:
            with closing(pdf.new_page(100, 200)) as page:
                page.set_rotation(rotation)
        pdf.save(source)
    provenance = {"source_sha256": probe.sha256(source), "page_count": 2, "blocks": []}
    (bundle / "provenance.json").write_text(json.dumps(provenance))
    (bundle / "document.md").write_text("No extracted text.\n")
    before = probe.bundle_hashes(bundle)
    reader = tmp_path / "fake-reader"
    reader.write_bytes(b"test reader")
    (tmp_path / "eng.traineddata").write_bytes(b"test trained data")
    monkeypatch.setattr(probe.shutil, "which", lambda _: str(reader))

    def run(command, **kwargs):
        if command[-1] == "--version":
            return SimpleNamespace(stdout="test reader 1.0\n")
        assert command[-7:] == ["--tessdata-dir", str(tmp_path), "--psm", "3", "-l", "eng", "tsv"]
        assert kwargs["env"]["OMP_THREAD_LIMIT"] == "2"
        if reader_result == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return SimpleNamespace(stdout=reader_result, stderr="", returncode=int(reader_result == "failure"))

    monkeypatch.setattr(probe.subprocess, "run", run)
    output = tmp_path / "review"
    report = probe.scan(bundle, output, tmp_path)
    assert report["summary"]["page_status"] == {expected: 1, "unsupported_rotation": 1}
    assert report["summary"]["line_status"] == (
        {"reader_disagreement": 1} if expected == "compared" else {}
    )
    assert probe.bundle_hashes(bundle) == before
    assert probe.sha256(source) == provenance["source_sha256"]
    assert json.loads((output / "report.json").read_text()) == report
    assert report["manifest_sha256"] == probe.sha256(output / "manifest.json")
    review = (output / "review.html").read_text()
    if expected == "compared":
        candidate, = report["pages"][0]["lines"]
        assert candidate["distance"] == 1
        assert candidate["review_crop"] in review
        assert (output / candidate["review_crop"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    else:
        assert "<section>" not in review
