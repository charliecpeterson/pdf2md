"""Run optional enrichment from a completed engine-neutral bundle state.

Inputs are completed versions; outputs go through the normal conversion pipeline
and therefore receive a fresh immutable version, audit artifacts, and provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
import json
from pathlib import Path

from pdf2md.cache import content_hash, doc_dir, latest_version
from pdf2md.config import Config
from pdf2md.engine_state import ENGINE_STATE_NAME, StoredEngine, load_engine_state
from pdf2md.pipeline import ConvertResult, convert_file
from pdf2md.schema import BlockType
from pdf2md.transcribe import get_transcriber


_STAGES = {"equations", "charts", "descriptions", "metadata"}


@dataclass(frozen=True)
class EnrichmentPreflight:
    source_version: Path
    source_pdf: Path
    stages: tuple[str, ...]
    pages: int
    equations: int
    equation_regions: int
    equation_transcriptions: int
    figures: int
    chart_datasets: int
    chart_model_candidates: int
    description_regions: int
    descriptions_present: int
    doi: str | None = None
    registry_metadata_present: bool = False


def resolve_version(path: Path, *, output_root: Path | None = None) -> Path:
    candidate = Path(path).expanduser().resolve()
    if candidate.is_dir() and (candidate / "provenance.json").is_file():
        return candidate
    if candidate.is_file() and candidate.suffix.casefold() == ".pdf":
        document_dir = doc_dir(
            content_hash(candidate), candidate, root=output_root
        )
    elif candidate.is_dir() and (candidate / "source.pdf").is_file():
        document_dir = candidate
    else:
        raise ValueError(
            "document must be a source PDF, a document directory, or a completed v<n> bundle"
        )
    version = latest_version(document_dir)
    if version is None:
        raise ValueError(f"no completed conversion found for {path}")
    return document_dir / f"v{version}"


def config_from_version(version_dir: Path, config_path: Path | None = None) -> Config:
    raw = json.loads((Path(version_dir) / "provenance.json").read_text())
    effective = (
        (raw.get("provenance") or {}).get("run_inputs", {}).get("effective_config", {})
    )
    known = {item.name for item in fields(Config)}
    base = Config(**{key: value for key, value in effective.items() if key in known})
    return Config.load(config_path, base=base)


def preflight(version_dir: Path, stages: tuple[str, ...]) -> EnrichmentPreflight:
    unknown = set(stages) - _STAGES
    if unknown or not stages:
        raise ValueError(
            "choose at least one enrichment stage: equations, charts, descriptions, or metadata"
        )
    version_dir = Path(version_dir).resolve()
    state = load_engine_state(version_dir)
    source_pdf = version_dir.parent / "source.pdf"
    if not source_pdf.is_file():
        raise ValueError(f"stored source PDF not found: {source_pdf}")
    provenance = json.loads((version_dir / "provenance.json").read_text())
    expected = provenance.get("source_sha256")
    if expected and content_hash(source_pdf) != expected:
        raise ValueError("stored source PDF hash does not match bundle provenance")
    state_path = version_dir / ENGINE_STATE_NAME
    if state_path.is_file():
        state_source = json.loads(state_path.read_text()).get("source_sha256")
        if expected and state_source != expected:
            raise ValueError("base state source hash does not match bundle provenance")
    equations = sum(block.type is BlockType.EQUATION for block in state.blocks)
    stored_blocks = provenance.get("blocks", [])
    stored_figures = provenance.get("figures", [])
    equation_blocks = [
        block for block in stored_blocks if block.get("type") == BlockType.EQUATION.value
    ]
    equation_regions = [
        block for block in equation_blocks if (block.get("extra") or {}).get("crop_path")
    ]
    equation_transcriptions = sum(
        bool((block.get("extra") or {}).get("transcribed"))
        for block in equation_regions
    )
    figures_with_assets = [figure for figure in stored_figures if figure.get("asset_path")]
    chart_datasets = sum(bool(figure.get("data_path")) for figure in figures_with_assets)
    description_block_regions = [
        block for block in stored_blocks
        if (block.get("extra") or {}).get("crop_path")
        and not (
            block.get("type") == BlockType.EQUATION.value
            and (block.get("extra") or {}).get("transcribed")
        )
    ]
    descriptions_present = sum(
        bool(figure.get("description")) for figure in figures_with_assets
    ) + sum(
        bool((block.get("extra") or {}).get("description"))
        for block in description_block_regions
    )
    manifest_path = version_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    doi = ((manifest.get("metadata") or {}).get("selected") or {}).get("doi")
    return EnrichmentPreflight(
        source_version=version_dir,
        source_pdf=source_pdf,
        stages=tuple(sorted(set(stages))),
        pages=len(state.page_sizes) or int(provenance.get("page_count", 0)),
        equations=equations,
        equation_regions=len(equation_regions),
        equation_transcriptions=equation_transcriptions,
        figures=len(stored_figures) or len(state.figures),
        chart_datasets=chart_datasets,
        chart_model_candidates=len(figures_with_assets) - chart_datasets,
        description_regions=len(figures_with_assets) + len(description_block_regions),
        descriptions_present=descriptions_present,
        doi=doi,
        registry_metadata_present=(version_dir / "data" / "doi-metadata.csl.json").is_file(),
    )


def enrich_version(
    version_dir: Path,
    stages: tuple[str, ...],
    *,
    config: Config | None = None,
    transcriber=None,
    describer=None,
) -> ConvertResult:
    plan = preflight(version_dir, stages)
    config = config or config_from_version(plan.source_version)
    changes = {}
    if "equations" in plan.stages:
        changes["transcribe_equations"] = True
    if "charts" in plan.stages:
        changes.update(digitize_figures=True, digitize_vlm=True)
    if "descriptions" in plan.stages:
        changes["describe_figures"] = True
    if "metadata" in plan.stages:
        changes["doi_metadata"] = True
    config = replace(config, **changes)

    if config.transcribe_equations and transcriber is None:
        transcriber = get_transcriber(config)
    engine = StoredEngine(plan.source_version, plan.stages)
    return convert_file(
        plan.source_pdf,
        engine=engine,
        transcriber=transcriber,
        describer=describer,
        config=config,
        output_root=plan.source_version.parent.parent,
    )
