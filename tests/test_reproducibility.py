"""Run and inference cache identities must change with output-shaping inputs."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from pdf2md.cache import matching_version, run_fingerprint
from pdf2md.config import Config
from pdf2md.describe import vision_cache_key
from pdf2md.engines.base import EngineResult
from pdf2md.pipeline import convert_file
from pdf2md.schema import BBox, Block, BlockType, FigureRef
from pdf2md.vision_cache import CacheStats, load_vision_cache, write_vision_cache


def test_config_rejects_unknown_keys(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("crop_dpi = 300\ncrop_dp1 = 300\n")

    with pytest.raises(ValueError, match=r"unknown configuration key\(s\): crop_dp1"):
        Config.load(path)


def test_config_rejects_retired_per_block_ocr_keys(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("ocr_vlm = true\npreprocess_scans = true\n")

    with pytest.raises(
        ValueError,
        match=r"unknown configuration key\(s\): ocr_vlm, preprocess_scans",
    ):
        Config.load(path)


def test_config_rejects_unknown_engine():
    with pytest.raises(ValueError, match="engine must be"):
        Config(engine="paddle")


def test_config_rejects_invalid_passage_settings():
    with pytest.raises(ValueError, match="passage_max_tokens must be positive"):
        Config(passage_max_tokens=0)
    with pytest.raises(ValueError, match="passage_tokenizer must be"):
        Config(passage_tokenizer="all-MiniLM-L6-v2")


def test_config_rejects_page_replacement_with_mineru():
    with pytest.raises(ValueError, match="discard MinerU's equation and table structure"):
        Config(engine="mineru", ocr_page_vlm=True)


def test_chart_recovery_defaults_match_bakeoff_policy():
    config = Config()

    assert config.digitize_figures is True
    assert config.digitize_vlm is False


def test_effective_config_omits_credentials_and_normalizes_model_path(tmp_path):
    config = Config(vlm_api_key="secret", local_model_dir=str(tmp_path / "models" / ".."))

    effective = config.effective_dict()

    assert "vlm_api_key" not in effective
    assert effective["local_model_dir"] == str(tmp_path.resolve())


def test_effective_config_hashes_table_reference(tmp_path):
    reference = tmp_path / "reference.csv"
    reference.write_text("atomic_number,row_key,column,value\n29,0.1,1S,0.3900\n")

    effective = Config(table_reference_path=str(reference)).effective_dict()

    assert effective["table_reference_path"] == str(reference.resolve())
    assert len(effective["table_reference_sha256"]) == 64


def test_run_fingerprint_is_canonical_and_input_sensitive():
    first = {"source_sha256": "abc", "config": {"crop_dpi": 220, "page_images": True}}
    reordered = {"config": {"page_images": True, "crop_dpi": 220}, "source_sha256": "abc"}
    changed = {"source_sha256": "abc", "config": {"crop_dpi": 300, "page_images": True}}

    assert run_fingerprint(first) == run_fingerprint(reordered)
    assert run_fingerprint(first) != run_fingerprint(changed)


def test_matching_version_selects_fingerprint_not_latest(tmp_path):
    for version, fingerprint in ((1, "first"), (2, "second"), (3, "first")):
        version_dir = tmp_path / f"v{version}"
        version_dir.mkdir()
        (version_dir / "provenance.json").write_text(json.dumps({
            "provenance": {"run_fingerprint": fingerprint}
        }))

    assert matching_version(tmp_path, "first") == 3
    assert matching_version(tmp_path, "second") == 2
    assert matching_version(tmp_path, "missing") is None


def test_matching_version_prefers_older_complete_run_over_newer_partial(tmp_path):
    complete = tmp_path / "v1"
    partial = tmp_path / "v2"
    complete.mkdir()
    partial.mkdir()
    (complete / "provenance.json").write_text(json.dumps({
        "provenance": {"run_fingerprint": "same", "run_metrics": {"stages": {}}}
    }))
    (partial / "provenance.json").write_text(json.dumps({
        "provenance": {
            "run_fingerprint": "same",
            "run_metrics": {
                "stages": {
                    "descriptions": {"counts": {"vision_failures": 1}}
                }
            },
        }
    }))

    assert matching_version(tmp_path, "same") == 1
    assert matching_version(tmp_path, "same", include_partial=True) == 2


def test_vision_cache_key_covers_prompt_and_generation_inputs(tmp_path):
    image = tmp_path / "crop.png"
    image.write_bytes(b"pixels")

    class Describer:
        def model_for(self, kind):
            return "model-a"

    describer = Describer()
    base = vision_cache_key(
        image, describer, "figure", context="caption", max_tokens=1024,
        endpoint="http://endpoint-a/v1",
    )

    assert base == vision_cache_key(
        image, describer, "figure", context="caption", max_tokens=1024,
        endpoint="http://endpoint-a/v1",
    )
    assert base != vision_cache_key(
        image, describer, "figure", context="different", max_tokens=1024,
        endpoint="http://endpoint-a/v1",
    )
    assert base != vision_cache_key(
        image, describer, "figure", context="caption", max_tokens=2048,
        endpoint="http://endpoint-a/v1",
    )
    assert base != vision_cache_key(
        image, describer, "figure", context="caption", max_tokens=1024,
        endpoint="http://endpoint-b/v1",
    )


def test_vlm_digitize_caches_complete_inference_identity(tmp_path):
    from pdf2md.digitize import vlm_digitize

    crop = tmp_path / "plot.png"
    crop.write_bytes(b"plot pixels")

    class Describer:
        calls = 0

        def model_for(self, kind):
            return "plot-reader"

        def describe(self, *args, **kwargs):
            self.calls += 1
            return '{"series":[{"points":[[0,1],[1,2]]}]}'

    describer = Describer()
    cache = {}
    first = vlm_digitize(
        crop, describer, cache=cache, endpoint="http://endpoint/v1", max_tokens=2048
    )
    second = vlm_digitize(
        crop, describer, cache=cache, endpoint="http://endpoint/v1", max_tokens=2048
    )

    assert first.series == second.series == [[(0.0, 1.0), (1.0, 2.0)]]
    assert describer.calls == 1
    assert len(cache) == 1


def test_vision_cache_reports_hits_misses_and_writes(tmp_path):
    stats = CacheStats()
    cache = load_vision_cache(tmp_path, stats)

    assert cache.get("missing") is None
    cache["stored"] = "answer"
    assert cache.get("stored") == "answer"
    write_vision_cache(tmp_path, cache)

    reloaded = load_vision_cache(tmp_path, stats)
    assert reloaded.get("stored") == "answer"
    assert stats.snapshot() == {"lookups": 3, "hits": 2, "misses": 1, "writes": 1}
    assert stats.since({"lookups": 1, "hits": 0, "misses": 1, "writes": 1}) == {
        "lookups": 2,
        "hits": 2,
        "misses": 0,
        "writes": 0,
    }


class _CountingEngine:
    name = "test"

    def __init__(self):
        self.calls = 0

    def convert(self, pdf_path: Path) -> EngineResult:
        self.calls += 1
        return EngineResult(
            blocks=[Block("#/text/0", BlockType.PARAGRAPH, "converted text", 1)],
            tables=[],
            figures=[],
            page_sizes={1: (612.0, 792.0)},
            engine_versions={"test": "1"},
        )


class _FigureEngine:
    name = "test-figure"

    def convert(self, pdf_path: Path) -> EngineResult:
        bbox = BBox(50.0, 300.0, 350.0, 50.0)
        block = Block("#/pictures/0", BlockType.FIGURE, "", 1, bbox=bbox)
        return EngineResult(
            blocks=[block],
            tables=[],
            figures=[FigureRef(block.id, 1, bbox)],
            page_sizes={1: (612.0, 792.0)},
            engine_versions={"test-figure": "1"},
        )


def test_convert_cache_selects_matching_run_not_latest(tmp_path, monkeypatch):
    monkeypatch.setenv("PDF2MD_OUT", str(tmp_path / "out"))
    pdf = Path(__file__).parent / "fixtures" / "vector_plot.pdf"
    engine = _CountingEngine()
    config = Config(
        do_formula_enrichment=False,
        detect_scripts=False,
        digitize_figures=False,
        ocr_figures=False,
        page_images=False,
    )

    first = convert_file(pdf, engine=engine, config=config)
    stored_source = first.out_dir.parent / "source.pdf"
    stored_mtime = stored_source.stat().st_mtime_ns
    same = convert_file(pdf, engine=engine, config=config)
    changed = convert_file(pdf, engine=engine, config=replace(config, crop_dpi=300))
    original_again = convert_file(pdf, engine=engine, config=config)

    assert first.out_dir.parent.name == f"vector-plot-{first.doc_id[:8]}"
    assert (first.version, first.cached) == (1, False)
    assert (same.version, same.cached) == (1, True)
    assert (changed.version, changed.cached) == (2, False)
    assert (original_again.version, original_again.cached) == (1, True)
    assert engine.calls == 2
    assert stored_source.read_bytes() == pdf.read_bytes()
    assert stored_source.stat().st_mtime_ns == stored_mtime

    document = json.loads((first.out_dir / "provenance.json").read_text())
    provenance = document["provenance"]
    conservation = json.loads((first.out_dir / "profile.json").read_text())[
        "numeric_conservation"
    ]["representation_aware"]
    assert conservation["scope"] == "enriched logical blocks to emitted Markdown"
    assert conservation["categories"]["unexplained_loss"] == {
        "words": 0,
        "numbers": 0,
    }
    assert conservation["categories"]["unexplained_addition"] == {
        "words": 0,
        "numbers": 0,
    }
    passages = [
        json.loads(line)
        for line in (first.out_dir / "passages.jsonl").read_text().splitlines()
    ]
    assert len(passages) == 1
    assert passages[0]["sources"][0]["block_id"] == "#/text/0"
    assert (first.out_dir / "passages.schema.json").exists()
    assert (first.out_dir / "base-state.json").exists()
    metadata = json.loads((first.out_dir / "metadata.json").read_text())
    assert metadata["schema_version"] == 1
    assert metadata["references"]["count"] == 0
    manifest = json.loads((first.out_dir / "manifest.json").read_text())
    assert manifest["read"]["metadata"] == "metadata.json"
    assert manifest["metadata"]["path"] == "metadata.json"
    outline = json.loads((first.out_dir / "outline.json").read_text())
    assert outline["schema_version"] == 2
    assert outline["outline"]["semantic_role"] == "document"
    assert outline["outline"]["passage_count"] == 1
    assert outline["markdown_files"][0]["path"] == "document.md"
    assert json.loads((first.out_dir / "symbols.json").read_text())["entries"] == []
    assert len(provenance["run_fingerprint"]) == 64
    assert len(provenance["run_inputs"]["implementation_sha256"]) == 64
    assert provenance["run_inputs"]["effective_config"] == config.effective_dict()
    assert provenance["run_inputs"]["engine"]["name"] == "test"
    assert provenance["derivation"] == {"kind": "base"}
    metrics = provenance["run_metrics"]
    assert metrics["duration_s"] == round(
        sum(stage["duration_s"] for stage in metrics["stages"].values()), 3
    )
    assert list(metrics["stages"]) == [
        "setup", "parse", "geometry", "render", "equations", "charts",
        "descriptions", "emit", "audit", "finalize",
    ]
    assert metrics["stages"]["charts"]["counts"] == {
        "enabled": False,
        "third_party_warning_types": 0,
        "third_party_warning_repeats": 0,
        "vision_cache_lookups": 0,
        "vision_cache_hits": 0,
        "vision_cache_misses": 0,
        "vision_cache_writes": 0,
        "attempted": 0,
        "accepted": 0,
        "declined": 0,
        "failed": 0,
        "ocr_axis_attempted": 0,
        "ocr_axis_ineligible": 0,
    }
    assert metrics["stages"]["audit"]["counts"]["passages"] == 1
    assert first.run_metrics == same.run_metrics == metrics
    readme = (first.out_dir / "README.md").read_text()
    assert "## Conversion work" in readme
    assert "Retrieval passages: 1" in readme
    assert "Exact stage timings and work counts are in `provenance.json`." in readme


def test_matching_run_with_failed_optional_calls_is_retried(tmp_path, monkeypatch):
    monkeypatch.setenv("PDF2MD_OUT", str(tmp_path / "out"))
    pdf = Path(__file__).parent / "fixtures" / "vector_plot.pdf"
    engine = _CountingEngine()
    config = Config(
        do_formula_enrichment=False,
        detect_scripts=False,
        digitize_figures=False,
        ocr_figures=False,
        page_images=False,
    )
    first = convert_file(pdf, engine=engine, config=config)
    provenance_path = first.out_dir / "provenance.json"
    document = json.loads(provenance_path.read_text())
    document["provenance"]["run_metrics"]["stages"]["descriptions"]["counts"][
        "vision_failures"
    ] = 2
    provenance_path.write_text(json.dumps(document))

    retried = convert_file(pdf, engine=engine, config=config)

    assert engine.calls == 2
    assert retried.version == 2
    assert retried.cached is False
    assert first.out_dir.is_dir()


def test_convert_reports_engine_setup_failure_without_raising(tmp_path, monkeypatch):
    monkeypatch.setenv("PDF2MD_OUT", str(tmp_path / "out"))

    def fail_setup(engine, config):
        raise RuntimeError("missing Python.h")

    monkeypatch.setattr("pdf2md.pipeline._get_engine", fail_setup)
    pdf = Path(__file__).parent / "fixtures" / "vector_plot.pdf"

    result = convert_file(pdf)

    assert result.failed
    assert result.error == "missing Python.h"
    assert result.version == 0


def test_chart_stage_collapses_repeated_ocr_warnings(tmp_path, monkeypatch, caplog):
    import logging

    monkeypatch.setenv("PDF2MD_OUT", str(tmp_path / "out"))

    def noisy_digitizer(*args, **kwargs):
        source = logging.getLogger("RapidOCR")
        source.warning("empty chart OCR result")
        source.warning("empty chart OCR result")
        source.warning("empty chart OCR result")
        return {
            "attempted": 0,
            "accepted": 0,
            "declined": 0,
            "failed": 0,
            "ocr_axis_attempted": 0,
            "ocr_axis_ineligible": 0,
        }

    monkeypatch.setattr("pdf2md.pipeline._digitize_figures", noisy_digitizer)
    config = Config(
        do_formula_enrichment=False,
        detect_scripts=False,
        digitize_figures=True,
        ocr_figures=False,
        page_images=False,
    )
    pdf = Path(__file__).parent / "fixtures" / "vector_plot.pdf"

    with caplog.at_level(logging.WARNING):
        converted = convert_file(pdf, engine=_CountingEngine(), config=config)

    chart_counts = converted.run_metrics["stages"]["charts"]["counts"]
    assert chart_counts["third_party_warning_types"] == 1
    assert chart_counts["third_party_warning_repeats"] == 2
    assert caplog.messages.count("empty chart OCR result") == 1
    assert any(
        "chart digitization: suppressed 2 repeated warning(s)" in message
        for message in caplog.messages
    )


def test_forced_versions_share_unchanged_asset_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("PDF2MD_OUT", str(tmp_path / "out"))
    pdf = Path(__file__).parent / "fixtures" / "vector_plot.pdf"
    config = Config(
        do_formula_enrichment=False,
        detect_scripts=False,
        digitize_figures=False,
        ocr_figures=False,
        page_images=False,
    )

    first = convert_file(pdf, engine=_FigureEngine(), config=config)
    second = convert_file(pdf, engine=_FigureEngine(), config=config, force=True)

    first_asset = next((first.out_dir / "assets").glob("*.png"))
    second_asset = next((second.out_dir / "assets").glob("*.png"))
    assert (first.version, second.version) == (1, 2)
    assert first_asset.samefile(second_asset)
