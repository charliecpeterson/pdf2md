"""Frozen scan sampling produces an unlabelled, detector-masked human review."""

import importlib.util
import json
import sys
from contextlib import closing
from pathlib import Path

import pypdfium2 as pdfium
import pytest

SPEC = importlib.util.spec_from_file_location(
    "prepare_scan_review", Path(__file__).parents[1] / "scripts/prepare_scan_review.py",
)
review = importlib.util.module_from_spec(SPEC)
# Dev helpers live in scripts rather than the installed package.
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
SPEC.loader.exec_module(review)


def fixture(tmp_path):
    bundle = tmp_path / "source" / "v1"
    bundle.mkdir(parents=True)
    source = bundle.parent / "source.pdf"
    with closing(pdfium.PdfDocument.new()) as pdf:
        for _ in range(3):
            pdf.new_page(100, 200).close()
        pdf.save(source)
    review.write_json(bundle / "review.json", {"items": [
        {"page": 1, "disposition": "action_required", "reason": "SECRET_BASELINE_HINT"},
    ]})
    (bundle / "document.md").write_text("Full output")
    (bundle / "passages.jsonl").write_text(json.dumps({
        "sources": [{"page": 1}], "display_text": "<script>unsafe()</script>",
    }) + "\n")
    reader = tmp_path / "reader"
    reader.mkdir()
    (reader / "page-001.tsv").write_text("pinned synthetic reader evidence")
    manifest = {
        "schema_version": 1, "source_sha256": review.sha256(source),
        "source": str(source), "bundle": str(bundle), "bundle_files": review.bundle_hashes(bundle),
        "policy": review.POLICY, "reader": {"version": "test reader"},
        "implementation_sha256": {"test": "frozen"},
    }
    review.write_json(reader / "manifest.json", manifest)
    report = {
        "schema_version": 1, "manifest_sha256": review.sha256(reader / "manifest.json"),
        "pages": [
            {"page": 1, "status": "compared", "lines": [{"status": "reader_disagreement"}],
             "reader_tsv_sha256": review.sha256(reader / "page-001.tsv")},
            {"page": 2, "status": "compared", "lines": [{"status": "short_line"}]},
            {"page": 3, "status": "reader_failed", "lines": []},
        ],
    }
    review.write_json(reader / "report.json", report)
    protocol = tmp_path / "protocol.md"
    protocol.write_text("Frozen synthetic protocol")
    sources = tmp_path / "sources.json"
    review.write_json(sources, {
        "protocol": str(protocol), "protocol_sha256": review.sha256(protocol),
        "sources": [{"name": "synthetic", "source_sha256": manifest["source_sha256"], "pages": 3}],
    })
    return sources, reader, bundle


def test_masked_review_keeps_refusals_and_never_invents_labels(tmp_path):
    sources, reader, bundle = fixture(tmp_path)
    before = review.bundle_hashes(bundle)
    output = tmp_path / "review"
    report = review.prepare(sources, [reader], output)
    assert report["summary"] == {
        "documents": 1, "pages": 3, "sampled_pages": 3, "baseline_flagged_pages": 1,
        "probe_flagged_pages": 1, "reader_status": {"compared": 2, "reader_failed": 1},
        "labels_completed": 0, "accuracy": None,
    }
    assert [(s["stratum"], s["population"], s["sampled"]) for s in report["strata"]] == [
        ("flagged", 1, 1), ("unflagged", 2, 2),
    ]
    assert [p["inclusion_probability"] for p in report["sample"]] == [1, 1, 1]
    html = (output / "review.html").read_text()
    for forbidden in ["SECRET_BASELINE_HINT", "reader_disagreement", "reader_failed", "Detector hints"]:
        assert forbidden not in html
    assert "&lt;script&gt;unsafe()&lt;/script&gt;" in html
    assert "<script>unsafe()</script>" not in html
    assert html.count('<section data-index=') == 3
    assert "document.md" in html
    labels = json.loads((output / "labels.json").read_text())
    assert labels["labels"] == [
        {"id": p["id"], "omission": "unreviewed", "reviewer": "", "note": ""}
        for p in report["sample"]
    ]
    scores = json.loads((output / "scores-pending.json").read_text())
    assert scores["reviewed_pages"] == 0
    assert scores["complete"] is False
    assert scores["fixed_budget"]["probe"]["omission_pages_found"] is None
    assert review.bundle_hashes(bundle) == before
    again = review.prepare(sources, [reader], tmp_path / "second")
    assert again == report


@pytest.mark.parametrize("target", ["bundle", "protocol", "reader_evidence"])
def test_changed_inputs_are_refused_before_review_creation(tmp_path, target):
    sources, reader, bundle = fixture(tmp_path)
    paths = {"bundle": bundle / "document.md", "protocol": tmp_path / "protocol.md",
             "reader_evidence": reader / "page-001.tsv"}
    paths[target].write_text("Changed")
    output = tmp_path / "review"
    with pytest.raises(ValueError, match="changed"):
        review.prepare(sources, [reader], output)
    assert not output.exists()


@pytest.mark.parametrize("mutation", ["missing_page", "duplicate_page", "manifest_hash", "policy"])
def test_invalid_reader_report_is_refused(tmp_path, mutation):
    sources, reader, _ = fixture(tmp_path)
    path = reader / "report.json"
    data = json.loads(path.read_text())
    if mutation == "missing_page":
        data["pages"].pop()
    elif mutation == "duplicate_page":
        data["pages"][1]["page"] = 1
    elif mutation == "manifest_hash":
        data["manifest_sha256"] = "wrong"
    else:
        manifest = json.loads((reader / "manifest.json").read_text())
        manifest["policy"]["minimum_characters"] = 1
        review.write_json(reader / "manifest.json", manifest)
        data["manifest_sha256"] = review.sha256(reader / "manifest.json")
    review.write_json(path, data)
    with pytest.raises(ValueError):
        review.prepare(sources, [reader], tmp_path / "review")


def test_input_directories_and_existing_outputs_are_protected(tmp_path):
    sources, reader, bundle = fixture(tmp_path)
    for output in [tmp_path, bundle.parent / "review", reader / "review"]:
        with pytest.raises(ValueError, match="output"):
            review.prepare(sources, [reader], output)
    with pytest.raises(ValueError, match="one reader"):
        review.prepare(sources, [], tmp_path / "review")


def test_duplicate_or_unexpected_source_is_refused(tmp_path):
    sources, reader, _ = fixture(tmp_path)
    data = json.loads(sources.read_text())
    data["sources"].append({"name": "other", "source_sha256": "other", "pages": 3})
    review.write_json(sources, data)
    with pytest.raises(ValueError, match="duplicate or unexpected"):
        review.prepare(sources, [reader, reader], tmp_path / "review")


def test_input_change_during_rendering_cannot_publish_a_completed_report(tmp_path, monkeypatch):
    sources, reader, bundle = fixture(tmp_path)
    original = review.write_review

    def changing_input(*args, **kwargs):
        original(*args, **kwargs)
        (bundle / "document.md").write_text("Changed during rendering")

    monkeypatch.setattr(review, "write_review", changing_input)
    output = tmp_path / "review"
    with pytest.raises(ValueError, match="changed"):
        review.prepare(sources, [reader], output)
    assert not (output / "report.json").exists()
    assert not (output / "labels.json").exists()


def test_existing_review_keeps_opt_in_visible_hints(tmp_path):
    sources, reader, _ = fixture(tmp_path)
    output = tmp_path / "review"
    report = review.prepare(sources, [reader], output)
    for page in report["sample"]:
        page.update(glyph_check="unavailable", candidates=[])
    review.write_review(report, output)
    assert "SECRET_BASELINE_HINT" in (output / "review.html").read_text()
