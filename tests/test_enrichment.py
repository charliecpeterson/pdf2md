"""Stored engine state and enrichment keep parser work reusable and auditable."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf2md.cache import content_hash
from pdf2md.config import Config
from pdf2md.engine_state import StoredEngine, load_engine_state, write_engine_state
from pdf2md.engines.base import EngineResult
from pdf2md.enrichment import (
    config_from_version,
    enrich_version,
    preflight,
    resolve_version,
)
from pdf2md.pipeline import ConvertResult, _transcribe_equations, convert_file
from pdf2md.schema import (
    BBox,
    Block,
    BlockType,
    FigureRef,
    RawCell,
    RawTable,
    TableData,
)


def _bundle(tmp_path: Path) -> Path:
    document = tmp_path / "paper-deadbeef"
    version = document / "v1"
    version.mkdir(parents=True)
    source = document / "source.pdf"
    source.write_bytes(b"pdf bytes")
    source_hash = content_hash(source)
    result = EngineResult(
        blocks=[
            Block(
                "#/equations/0",
                BlockType.EQUATION,
                "",
                1,
                BBox(1, 8, 9, 2),
                extra={"crop_path": "assets/equation.png"},
            ),
            Block("#/pictures/0", BlockType.FIGURE, "", 1, BBox(2, 7, 8, 3)),
            Block(
                "#/tables/0",
                BlockType.TABLE,
                "",
                2,
                BBox(1, 8, 9, 2),
                extra={"crop_path": "assets/table.png"},
            ),
        ],
        tables=[TableData("#/tables/0", 2, BBox(1, 8, 9, 2), "| a |\n| - |")],
        figures=[
            FigureRef(
                "#/pictures/0",
                1,
                BBox(2, 7, 8, 3),
                asset_path="assets/figure.png",
            )
        ],
        page_sizes={1: (612.0, 792.0), 2: (612.0, 792.0)},
        engine_versions={"fixture": "1"},
        quality_evidence={"layout": "fixture"},
        raw_tables={
            "#/tables/0": RawTable(
                [RawCell("a", BBox(1, 8, 9, 2), 0, 0, 1, 1, True)], 1, 1
            )
        },
    )
    write_engine_state(version, source_hash, result)
    state = json.loads((version / "base-state.json").read_text())
    (version / "provenance.json").write_text(json.dumps({
        "doc_id": source_hash,
        "source_sha256": source_hash,
        "page_count": 2,
        "blocks": state["blocks"],
        "tables": state["tables"],
        "figures": state["figures"],
        "provenance": {
            "run_inputs": {"effective_config": Config().effective_dict()}
        },
    }))
    return version


def test_engine_state_round_trip_preserves_transient_table_cells(tmp_path):
    version = _bundle(tmp_path)

    restored = load_engine_state(version)

    assert restored.page_sizes == {1: (612.0, 792.0), 2: (612.0, 792.0)}
    assert restored.blocks[0].type is BlockType.EQUATION
    assert restored.figures[0].bbox == BBox(2, 7, 8, 3)
    assert restored.raw_tables["#/tables/0"].cells[0].header is True


def test_preflight_resolves_document_and_refuses_changed_source(tmp_path):
    version = _bundle(tmp_path)

    assert resolve_version(version.parent) == version
    plan = preflight(version, ("descriptions", "equations"))
    assert (
        plan.pages,
        plan.equations,
        plan.equation_regions,
        plan.figures,
        plan.chart_model_candidates,
        plan.description_regions,
    ) == (2, 1, 1, 1, 1, 3)

    (version.parent / "source.pdf").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash does not match"):
        preflight(version, ("equations",))


def test_preflight_refuses_state_from_another_source(tmp_path):
    version = _bundle(tmp_path)
    state_path = version / "base-state.json"
    state = json.loads(state_path.read_text())
    state["source_sha256"] = "0" * 64
    state_path.write_text(json.dumps(state))

    with pytest.raises(ValueError, match="base state source hash"):
        preflight(version, ("charts",))


def test_config_overlay_keeps_source_settings(tmp_path):
    version = _bundle(tmp_path)
    raw = json.loads((version / "provenance.json").read_text())
    raw["provenance"]["run_inputs"]["effective_config"].update({
        "force_ocr": True,
        "crop_dpi": 300,
    })
    (version / "provenance.json").write_text(json.dumps(raw))
    overlay = tmp_path / "enrich.toml"
    overlay.write_text('vlm_model = "reader"\n')

    config = config_from_version(version, overlay)

    assert config.force_ocr is True
    assert config.crop_dpi == 300
    assert config.vlm_model == "reader"


def test_missing_optional_equation_service_leaves_base_bundle_unchanged(
    tmp_path, monkeypatch
):
    version = _bundle(tmp_path)
    provenance_before = (version / "provenance.json").read_bytes()

    def unavailable(config):
        raise RuntimeError("surya unavailable")

    monkeypatch.setattr("pdf2md.enrichment.get_transcriber", unavailable)

    with pytest.raises(RuntimeError, match="surya unavailable"):
        enrich_version(version, ("equations",), config=Config())

    assert (version / "provenance.json").read_bytes() == provenance_before
    assert sorted(path.name for path in version.parent.glob("v*")) == ["v1"]


def test_enrich_uses_stored_engine_and_same_document_root(tmp_path, monkeypatch):
    version = _bundle(tmp_path)
    seen = {}

    def fake_convert(pdf_path, **kwargs):
        seen.update(pdf_path=pdf_path, **kwargs)
        return ConvertResult("doc", 2, version.parent / "v2", [])

    monkeypatch.setattr("pdf2md.enrichment.convert_file", fake_convert)
    result = enrich_version(
        version,
        ("charts", "descriptions"),
        config=Config(do_formula_enrichment=False),
    )

    assert result.version == 2
    assert seen["output_root"] == version.parent.parent
    assert seen["config"].digitize_vlm is True
    assert seen["config"].describe_figures is True
    assert isinstance(seen["engine"], StoredEngine)
    assert seen["engine"].derivation["source_version"] == "v1"
    assert seen["engine"].derivation["stages"] == ["charts", "descriptions"]


def test_metadata_enrichment_reuses_stored_parse_and_enables_registry_lookup(
    tmp_path, monkeypatch
):
    version = _bundle(tmp_path)
    seen = {}

    def fake_convert(pdf_path, **kwargs):
        seen.update(pdf_path=pdf_path, **kwargs)
        return ConvertResult("doc", 2, version.parent / "v2", [])

    monkeypatch.setattr("pdf2md.enrichment.convert_file", fake_convert)

    enrich_version(version, ("metadata",), config=Config())

    assert seen["config"].doi_metadata is True
    assert isinstance(seen["engine"], StoredEngine)
    assert seen["engine"].derivation["stages"] == ["metadata"]


def test_completed_enrichment_records_derived_provenance(tmp_path, monkeypatch):
    class Engine:
        name = "fixture"

        def __init__(self):
            self.calls = 0

        def convert(self, pdf_path):
            self.calls += 1
            return EngineResult(
                blocks=[Block("#/text/0", BlockType.PARAGRAPH, "stored text", 1)],
                tables=[],
                figures=[],
                page_sizes={1: (612.0, 792.0)},
                engine_versions={"fixture": "1"},
            )

    class Describer:
        calls = 0
        failures = 0

        def model_for(self, kind):
            return "fixture"

        def describe(self, *args, **kwargs):
            raise AssertionError("bundle has no crop regions")

    monkeypatch.setenv("PDF2MD_OUT", str(tmp_path / "out"))
    source = Path(__file__).parent / "fixtures" / "vector_plot.pdf"
    engine = Engine()
    config = Config(
        do_formula_enrichment=False,
        detect_scripts=False,
        digitize_figures=False,
        ocr_figures=False,
        page_images=False,
    )
    base = convert_file(source, engine=engine, config=config)

    derived = enrich_version(
        base.out_dir,
        ("descriptions",),
        config=config,
        describer=Describer(),
    )

    assert engine.calls == 1
    assert derived.version == 2
    provenance = json.loads((derived.out_dir / "provenance.json").read_text())["provenance"]
    assert provenance["derivation"]["kind"] == "enrichment"
    assert provenance["derivation"]["source_version"] == "v1"
    assert provenance["derivation"]["stages"] == ["descriptions"]
    assert len(provenance["derivation"]["base_state_sha256"]) == 64
    assert (derived.out_dir / "base-state.json").is_file()


class _InterruptingTranscriber:
    cache_identity = "fixture-transcriber-v1"

    def __init__(self, fail_on: int | None = None) -> None:
        self.calls = 0
        self.fail_on = fail_on

    def transcribe(self, image_path: Path) -> str:
        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeError("interrupted")
        return image_path.stem


def test_interrupted_equation_enrichment_reuses_completed_regions(tmp_path):
    document = tmp_path / "document"
    version = document / "v2"
    assets = version / "assets"
    assets.mkdir(parents=True)
    for name in ("first.png", "second.png"):
        (assets / name).write_bytes(name.encode())
    blocks = [
        Block(
            f"#/equations/{index}",
            BlockType.EQUATION,
            "",
            1,
            extra={"crop_path": f"assets/{name}"},
        )
        for index, name in enumerate(("first.png", "second.png"))
    ]
    interrupted = _InterruptingTranscriber(fail_on=2)

    with pytest.raises(RuntimeError, match="interrupted"):
        _transcribe_equations(blocks, interrupted, version, document)

    rerun_blocks = [
        Block(
            f"#/equations/{index}",
            BlockType.EQUATION,
            "",
            1,
            extra={"crop_path": f"assets/{name}"},
        )
        for index, name in enumerate(("first.png", "second.png"))
    ]
    resumed = _InterruptingTranscriber()
    _transcribe_equations(rerun_blocks, resumed, version, document)

    assert resumed.calls == 1
    assert rerun_blocks[0].extra["transcribed"] == "first"
    assert rerun_blocks[1].extra["transcribed"] == "second"
