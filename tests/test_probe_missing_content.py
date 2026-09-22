"""The geometry probe refuses unsupported evidence and never labels its own output."""

import ctypes
import importlib.util
import json
from contextlib import closing
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as raw
import pytest
from PIL import Image

SPEC = importlib.util.spec_from_file_location(
    "probe_missing_content", Path(__file__).parents[1] / "scripts/probe_missing_content.py"
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
inspect_page = probe.inspect_page


def line(text="Missing paragraph", x=100, y=300):
    return [(char, x + i * 6, y, x + i * 6 + 5, y + 10)
            for i, char in enumerate(text)]


def block(box=(90, 320, 250, 290), status="emitted"):
    return {"id": "block-1", "page": 1, "coverage_status": status,
            "bbox": dict(zip(("x0", "y0", "x1", "y1"), box)) if box else None}


def test_missing_line_exact_evidence_and_reversed_box():
    result = inspect_page((0, 0, 600, 800), line(), [block()], 0.02)
    assert (result["visible_glyphs"], result["uncovered_glyphs"]) == (16, 0)
    assert result["candidates"] == []
    missing = inspect_page((0, 0, 600, 800), line(), [block(status="dropped")], 0.02)
    assert missing["candidates"][1] == {
        "kind": "uncovered_glyphs", "bbox": [100, 300, 201, 310],
        "characters": 16, "text_hint": "Missing paragraph", "actionable": True,
        "furniture_edge": None, "edge_distance": .375,
    }


def furniture_page(number, *, text="Repeated running header", y=730, body_y=600):
    result = inspect_page((0, 0, 600, 800), line(text, y=y) + line("Body text content", y=body_y),
                          [block((90, body_y + 20, 500, body_y - 10))], .02)
    return {"page": number, "probe_flagged": any(c["actionable"] for c in result["candidates"]),
            **result}


def test_running_headers_are_retained_with_document_evidence():
    pages = [furniture_page(n) for n in range(1, 5)]
    probe.filter_running_furniture(pages)
    assert [p["probe_flagged_before_furniture"] for p in pages] == [True] * 4
    assert [p["probe_flagged"] for p in pages] == [False] * 4
    assert pages[0]["candidates"][0]["kind"] == "probable_running_furniture"
    assert pages[0]["candidates"][0]["furniture_evidence"]["pages"] == [1, 2, 3, 4]


@pytest.mark.parametrize("change", ["few_pages", "same_page", "position", "numbers", "body", "table"])
def test_furniture_negative_controls_stay_flagged(change):
    pages = [furniture_page(n) for n in range(1, 4)]
    if change == "few_pages":
        pages.pop()
    elif change == "same_page":
        pages = [furniture_page(1) for _ in range(3)]
    elif change == "position":
        pages[2] = furniture_page(3, y=710)
    elif change == "numbers":
        pages = [furniture_page(n, text=f"Repeated header value {n}") for n in range(1, 4)]
    elif change == "body":
        pages[0]["body_line_keys"].append("repeatedrunningheader")
    else:
        pages = [furniture_page(n, body_y=713) for n in range(1, 4)]
    probe.filter_running_furniture(pages)
    assert all(p["probe_flagged"] for p in pages)


def test_repeated_footers_and_body_omission_are_separate():
    pages = [furniture_page(n, y=50) for n in range(1, 4)]
    pages[0]["candidates"].append({"kind": "uncovered_glyphs", "actionable": True,
                                    "text_hint": "A genuinely missing paragraph"})
    probe.filter_running_furniture(pages)
    assert [p["probe_flagged"] for p in pages] == [True, False, False]


def test_continuation_coverage_does_not_hide_an_unrepresented_paragraph():
    primary = block((90, 120, 250, 90))
    primary["source_spans"] = [
        {"page": 1, "bbox": primary["bbox"]},
        {"page": 2, "bbox": block()["bbox"]},
    ]
    pages = probe.page_blocks([primary])
    assert sorted(pages) == [1, 2]
    result = inspect_page((0, 0, 600, 800), line() + line(y=200), pages[2], .02)
    assert (result["visible_glyphs"], result["uncovered_glyphs"]) == (32, 16)
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["bbox"] == [100, 200, 201, 210]
    assert result["candidates"][0]["actionable"] is True


@pytest.mark.parametrize("status,crop", [("cropped", None), ("flagged", None),
                                         ("dropped", None), ("emitted", "assets/first.png")])
def test_primary_crop_or_marker_does_not_cover_continuation(status, crop):
    primary = block(status=status)
    primary["source_spans"] = [{"page": 1, "bbox": primary["bbox"]},
                               {"page": 2, "bbox": primary["bbox"]}]
    primary["extra"] = {"crop_path": crop} if crop else {}
    pages = probe.page_blocks([primary])
    assert sorted(pages) == [1]
    result = inspect_page((0, 0, 600, 800), line(), pages[2], .02)
    assert result["uncovered_glyphs"] == 16
    assert any(c["actionable"] for c in result["candidates"])


@pytest.mark.parametrize("status", ["emitted", "cropped", "flagged"])
def test_content_crop_is_represented(status):
    result = inspect_page((0, 0, 600, 800), line(), [block(status=status)], 0.02)
    assert result["uncovered_glyphs"] == 0


def test_margin_and_short_run_remain_evidence_not_flags():
    result = inspect_page((10, 20, 610, 820), line(y=21) + line("short"), [block((20, 90, 40, 70))], 0.02)
    assert [(c["kind"], c["actionable"]) for c in result["candidates"]] == [
        ("uncovered_glyphs", False), ("margin_glyphs", False)]


def test_geometry_covers_union_and_refuses_unlocated_blocks():
    chars = line("abcdefghijklmnopqrst")
    result = inspect_page((0, 0, 600, 800), chars,
                          [block((90, 290, 160, 320)), block((160, 290, 240, 320))], .1)
    assert result["uncovered_glyphs"] == 0
    result = inspect_page((0, 0, 600, 800), chars, [block(None)], .1)
    assert result["glyph_check"] == "unlocated_representation"
    assert result["candidates"] == []
    assert result["unlocated_block_ids"] == ["block-1"]


def test_scan_nonblank_page_only_not_partial_content():
    result = inspect_page((0, 0, 600, 800), None, [], .02)
    assert result["glyph_check"] == "unavailable"
    assert result["visible_glyphs"] is None
    assert [c["kind"] for c in result["candidates"]] == ["nonblank_page_without_representation"]
    assert inspect_page((0, 0, 600, 800), None, [], 0)["candidates"] == []
    assert inspect_page((0, 0, 600, 800), None, [block()], .02)["candidates"] == []


def test_off_page_geometry_is_unlocated_not_coverage():
    result = inspect_page((0, 0, 600, 800), line(), [block((700, 900, 800, 950))], .02)
    assert result["glyph_check"] == "unlocated_representation"
    assert result["represented_boxes"] == 0
    assert result["candidates"] == []


def test_columns_do_not_form_one_substantial_run():
    result = inspect_page((0, 0, 600, 800), line("short") + line("short", x=400), [block((1, 1, 3, 3))], .1)
    assert [c["characters"] for c in result["candidates"]] == [5, 5]
    assert not any(c["actionable"] for c in result["candidates"])


def fixture_bundle(tmp_path, rotation=0, force_ocr=False):
    bundle = tmp_path / "fixture" / "v1"
    bundle.mkdir(parents=True)
    source = bundle.parent / "source.pdf"
    with closing(pdfium.PdfDocument.new()) as pdf:
        page = pdf.new_page(600, 800)
        text = raw.FPDFPageObj_NewTextObj(pdf.raw, b"Helvetica", 12)
        content = (ctypes.c_ushort * 18)(*map(ord, "Missing paragraph"), 0)
        assert raw.FPDFText_SetText(text, content)
        raw.FPDFPageObj_Transform(text, 1, 0, 0, 1, 100, 300)
        page.insert_obj(pdfium.PdfObject(text))
        page.gen_content()
        page.set_rotation(rotation)
        page.close()
        pdf.save(source)
    probe.write_json(bundle / "provenance.json", {
        "source_sha256": probe.sha256(source), "page_count": 1, "blocks": [],
        "provenance": {"run_inputs": {"effective_config": {"force_ocr": force_ocr}}},
    })
    probe.write_json(bundle / "review.json", {"items": []})
    (bundle / "document.md").write_text("No content.\n")
    (bundle / "passages.jsonl").write_text(json.dumps({
        "sources": [{"page": 1}], "display_text": "<script>unsafe()</script>",
    }) + "\n")
    return bundle


def test_real_pdf_end_to_end_and_input_immutability(tmp_path):
    bundle = fixture_bundle(tmp_path)
    before = probe.bundle_hashes(bundle)
    source_before = probe.sha256(bundle.parent / "source.pdf")
    manifest = tmp_path / "manifest.json"
    probe.freeze([bundle], manifest, "development", "synthetic")
    output = tmp_path / "review"
    report = probe.scan(manifest, output, 2, 42)
    assert report["summary"] == {
        "documents": 1, "pages": 1, "baseline_flagged_pages": 0,
        "probe_flagged_pages": 1, "newly_flagged_pages": 1,
        "probe_flagged_before_furniture": 1, "probable_furniture_candidates": 0,
        "glyph_check_status": {"measured": 1}, "sampled_pages": 1,
        "labelled_pages": 0, "accuracy": None,
    }
    assert report["pages"][0]["uncovered_glyphs"] == 16
    with Image.open(output / "page-0000.png") as image:
        assert image.size == (1200, 1600)
    review_html = (output / "review.html").read_text()
    assert "&lt;script&gt;unsafe()&lt;/script&gt;" in review_html
    assert "<script>unsafe()</script>" not in review_html
    labels = json.loads((output / "labels.json").read_text())
    scores = probe.score(report, labels, 1)
    assert scores["reviewed_pages"] == 0
    assert scores["estimated_baseline_unflagged_omission_page_rate"] is None
    assert scores["complete"] is False
    assert scores["fixed_budget"]["probe"]["omission_pages_found"] is None
    assert probe.bundle_hashes(bundle) == before
    assert probe.sha256(bundle.parent / "source.pdf") == source_before
    with pytest.raises(ValueError, match="new output directory"):
        probe.scan(manifest, output, 2, 42)
    (bundle / "document.md").write_text("Changed")
    with pytest.raises(ValueError, match="pinned bundle changed"):
        probe.scan(manifest, tmp_path / "other", 2, 42)
    assert not (tmp_path / "other").exists()


def test_review_links_split_output(tmp_path):
    bundle = fixture_bundle(tmp_path)
    (bundle / "document.md").rename(bundle / "index.md")
    (bundle / "01_part.md").write_text("Extracted text")
    (bundle / "passages.jsonl").write_text(json.dumps({
        "sources": [{"page": 1}], "display_text": "Extracted text", "markdown": "01_part.md",
    }) + "\n")
    manifest = tmp_path / "manifest.json"
    probe.freeze([bundle], manifest, "development", "synthetic")
    probe.scan(manifest, tmp_path / "review", 1, 42)
    text = (tmp_path / "review/review.html").read_text()
    assert 'v1/index.md"' in text
    assert 'v1/01_part.md"' in text
    assert 'v1/document.md"' not in text


def test_duplicate_sources_and_changed_implementation_refused(tmp_path):
    bundle = fixture_bundle(tmp_path)
    with pytest.raises(ValueError, match="one conversion per source"):
        probe.freeze([bundle, bundle], tmp_path / "duplicate.json", "development", "synthetic")
    assert not (tmp_path / "duplicate.json").exists()
    manifest_path = tmp_path / "manifest.json"
    manifest = probe.freeze([bundle], manifest_path, "development", "synthetic")
    manifest["implementation_sha256"] = {}
    probe.write_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="implementation changed"):
        probe.scan(manifest_path, tmp_path / "review", 1, 42)


@pytest.mark.parametrize("rotation,force_ocr,expected", [
    (90, False, "unsupported_page_rotation"), (0, True, "unavailable"),
])
def test_refusal_and_force_ocr_from_run_identity(tmp_path, rotation, force_ocr, expected):
    bundle = fixture_bundle(tmp_path, rotation, force_ocr)
    manifest = probe.freeze([bundle], tmp_path / "manifest.json", "development", "synthetic")
    pages = probe.scan_document(manifest["documents"][0])
    assert pages[0]["glyph_check"] == expected
    if rotation:
        assert pages[0]["probe_flagged"] is False
    else:
        assert [c["kind"] for c in pages[0]["candidates"]] == ["nonblank_page_without_representation"]


def sample_frame():
    return [{"id": f"d:p{n}", "document_id": "d", "page": n,
             "baseline_flagged": n == 1, "probe_flagged": n <= 2}
            for n in range(1, 7)]


def test_sampling_is_deterministic_and_includes_unflagged():
    selected, strata = probe.sample_pages(sample_frame(), 1, 42)
    assert (selected, strata) == probe.sample_pages(list(reversed(sample_frame())), 1, 42)
    assert [s["stratum"] for s in selected] == ["flagged", "unflagged"]
    assert [s["inclusion_probability"] for s in selected] == [.5, .25]


def test_scoring_weights_and_incomplete_labels():
    sample = [{**p, "inclusion_probability": .5 if p["page"] == 2 else .25}
              for p in sample_frame() if p["page"] in {2, 3}]
    report = {"experiment_id": "test", "sample": sample, "seed": 42}
    labels = {"schema_version": 1, "experiment_id": "test", "labels": [
        {"id": "d:p2", "omission": "yes", "reviewer": "human", "note": "Missing paragraph at top."},
        {"id": "d:p3", "omission": "no", "reviewer": "human", "note": "All content represented."},
    ]}
    scores = probe.score(report, labels, 1)
    assert scores["estimated_baseline_unflagged_omission_page_rate"] == pytest.approx(1 / 3)
    assert scores["fixed_budget"]["probe"]["omission_pages_found"] == 1
    assert scores["complete"] is True
    labels["labels"][1]["omission"] = "uncertain"
    assert probe.score(report, labels, 1)["estimated_baseline_unflagged_omission_page_rate"] is None
    labels["labels"][0]["note"] = ""
    with pytest.raises(ValueError, match="source evidence note"):
        probe.score(report, labels, 1)
    labels["experiment_id"] = "wrong"
    with pytest.raises(ValueError, match="different experiment"):
        probe.score(report, labels, 1)


def test_finite_population_bounds_do_not_treat_unreviewed_pages_as_clean():
    pages = sample_frame()
    sample = [{**p, "inclusion_probability": .5} for p in pages if p["page"] in {2, 3}]
    report = {"experiment_id": "bounds", "sample": sample, "pages": pages, "seed": 42}
    labels = {"schema_version": 1, "experiment_id": "bounds", "labels": [
        {"id": "d:p2", "omission": "yes", "reviewer": "test", "note": "Source paragraph absent."},
        {"id": "d:p3", "omission": "no", "reviewer": "test", "note": "Source page represented."},
    ]}
    result = probe.score(report, labels, 1)
    bounds = result["baseline_unflagged_finite_corpus_bounds"]
    assert (bounds["population_pages"], bounds["labelled_pages"]) == (5, 2)
    assert (bounds["lower"], bounds["upper"]) == (.2, .8)
    assert result["uncertainty_interval"] is None
    assert result["reviewed_sample_screening"]["probe"] == {
        "true_positive_pages": 1, "false_positive_pages": 0,
        "false_negative_pages": 0, "true_negative_pages": 1,
    }
    labels["labels"][1]["omission"] = "uncertain"
    assert probe.score(report, labels, 1)["baseline_unflagged_finite_corpus_bounds"]["upper"] == 1
