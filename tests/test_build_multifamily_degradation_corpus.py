"""The multi-family corpus keeps source identity and degradation scope explicit."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

from PIL import Image


SCRIPTS = Path(__file__).parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location(
    "build_multifamily_degradation_corpus",
    SCRIPTS / "build_multifamily_degradation_corpus.py",
)
corpus = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(corpus)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_magick_operations_keep_requested_order():
    operations, jpeg_quality = corpus._magick_operations(
        ["scale:0.5", "contrast:-35", "rotate:1.5", "jpeg:25"],
        200,
        100,
    )

    assert operations == [
        "-filter", "Lanczos", "-resize", "100x50!", "-resize", "200x100!",
        "-brightness-contrast", "0x-35",
        "-background", "white", "-rotate", "1.5", "-gravity", "center",
        "-crop", "200x100+0+0", "+repage",
    ]
    assert jpeg_quality == "25"


def test_adaptive_binarization_preserves_dark_text_polarity():
    operations, jpeg_quality = corpus._magick_operations(
        ["lat:25x25+5%"],
        200,
        100,
    )

    assert operations == [
        "-colorspace", "Gray", "-lat", "25x25+5%", "-negate",
    ]
    assert jpeg_quality is None


def test_select_preserves_manifest_order_and_rejects_unknown_ids():
    records = [{"id": "first"}, {"id": "second"}, {"id": "third"}]

    assert corpus._select(records, ["third", "first"], "record") == [
        {"id": "first"},
        {"id": "third"},
    ]

    try:
        corpus._select(records, ["missing"], "record")
    except ValueError as error:
        assert str(error) == "unknown record: missing"
    else:
        raise AssertionError("unknown IDs must be rejected")


def test_build_pins_selected_sources_and_pages(tmp_path, monkeypatch):
    source_crop = tmp_path / "source.png"
    Image.new("L", (40, 20), "white").save(source_crop)
    label_artifact = tmp_path / "labels.json"
    label_artifact.write_text("{}\n")
    sources_path = tmp_path / "sources.json"
    sources_path.write_text(json.dumps({
        "schema_version": 1,
        "artifacts": {
            "labels": {
                "path": str(label_artifact),
                "sha256": _sha256(label_artifact),
            }
        },
        "families": [{
            "id": "family-a",
            "source_crop": str(source_crop),
            "source_crop_sha256": _sha256(source_crop),
            "typography": "test",
            "features": ["decimal"],
        }],
    }))
    output = tmp_path / "corpus.pdf"

    monkeypatch.setattr(corpus.shutil, "which", lambda name: f"/{name}")
    monkeypatch.setattr(corpus, "_tool_version", lambda command: "ImageMagick test")

    def render_variant(magick, source, destination, operations):
        shutil.copyfile(source, destination)
        return ["resolved", *operations]

    monkeypatch.setattr(corpus, "_render_variant", render_variant)
    manifest = corpus.build(
        sources_path,
        output,
        tmp_path / "work",
        family_ids=["family-a"],
        variant_ids=["clean", "jpeg_25"],
    )

    assert output.is_file()
    assert manifest["sources"] == str(sources_path)
    assert manifest["sources_sha256"] == _sha256(sources_path)
    assert [page["page"] for page in manifest["pages"]] == [1, 2]
    assert [page["variant"] for page in manifest["pages"]] == ["clean", "jpeg_25"]
    assert manifest["corpus_sha256"] == _sha256(output)
