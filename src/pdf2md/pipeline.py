"""Orchestration: PDF → engine → structure → render ∥ emit → coverage → disk.

`convert_file` is idempotent (content-hash identity, readable versioned output,
no-op unless `force`). `convert_dir` isolates failures per document so one bad
PDF never aborts a batch.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cache
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

import pypdfium2 as pdfium

from pdf2md import __version__
from pdf2md.bookmarks import read_bookmarks
from pdf2md.cache import (
    content_hash,
    deduplicate_assets,
    doc_dir,
    is_document_dir,
    matching_version,
    next_version,
    out_root,
    run_fingerprint,
)
from pdf2md.chunks import write_chunks
from pdf2md.confidence import RECOVER_BELOW
from pdf2md.config import Config
from pdf2md.describe import (
    VISION_CACHE_SCHEMA_VERSION,
    VISION_PROMPT_SHA256,
    Describer,
    get_describer,
)
from pdf2md.document_map import write_document_map
from pdf2md.document_metadata import (
    build_document_metadata,
    write_document_metadata,
)
from pdf2md.enrich import (
    GlyphIndex,
    enrich_blocks,
    enrich_figures,
    enrich_tables,
    recall_review_flags,
    record_recall,
    resegment_ocr_prose,
)
from pdf2md.conservation import (
    annotate_conservation_warnings,
    conservation_review_flags,
    numeric_conservation,
)
from pdf2md.coverage import build_report
from pdf2md.emit import emit_document
from pdf2md.engine_state import write_engine_state
from pdf2md.engines.base import Engine, normalize_page_origin
from pdf2md.logging import Progress, collapse_repeated_warnings, get_logger
from pdf2md.doi_metadata import (
    DOI_METADATA_NAME,
    fetch_doi_metadata,
    merge_doi_metadata,
)
from pdf2md.metadata import extract_metadata
from pdf2md.passages import write_passages
from pdf2md.passage_tokenizer import load_passage_tokenizer
from pdf2md.profile import build_profile, write_manifest, write_profile, write_readme
from pdf2md.render import CropRenderer, dpi_for_region
from pdf2md.review import build_review_queue, write_review_files
from pdf2md.run_metrics import RunMetrics, failed_optional_calls
from pdf2md.scan_ocr import _vlm_ocr_pages
from pdf2md.schema import (
    FORMAT_VERSION,
    BlockType,
    CoverageReport,
    Document,
    Provenance,
)
from pdf2md.transcribe import Transcriber, get_transcriber
from pdf2md.reading_order import reading_order_flags
from pdf2md.structure import build_structure
from pdf2md.symbol_index import write_symbol_index
from pdf2md.table_artifacts import annotate_table_artifacts
from pdf2md.table_audit import raster_row_findings
from pdf2md.table_rebuild import glyph_unbacked_tables
from pdf2md.tables import gfm_rows
from pdf2md.visual import (
    _describe_crops,
    _digitize_figures,
    _label_figures,
    _ocr_scanned_figures,
    _promote_figure_captions,
    associate_figure_captions,
    _svg_figures,
    clean_figure_structure,
)
from pdf2md.vision_cache import CacheStats, load_vision_cache

log = get_logger("pipeline")
_OCR_LOGGERS = ("RapidOCR", "docling.models.stages.ocr.rapid_ocr_model")


@dataclass
class ConvertResult:
    doc_id: str
    version: int
    out_dir: Path
    md_files: list[Path]
    coverage: CoverageReport | None = None
    page_count: int = 0
    cached: bool = False
    failed: bool = False
    error: str | None = None
    run_metrics: dict = field(default_factory=dict)


_RUN_INPUTS_SCHEMA_VERSION = 1


def _store_source(pdf_path: Path, document_dir: Path, expected_sha256: str) -> Path:
    document_dir.mkdir(parents=True, exist_ok=True)
    stored = document_dir / "source.pdf"
    if stored.is_file() and content_hash(stored) == expected_sha256:
        return stored

    pending = document_dir / "source.pdf.tmp"
    try:
        shutil.copyfile(pdf_path, pending)
        actual_sha256 = content_hash(pending)
        if actual_sha256 != expected_sha256:
            raise OSError(
                f"stored source hash {actual_sha256} != expected {expected_sha256}"
            )
        pending.replace(stored)
    finally:
        pending.unlink(missing_ok=True)
    return stored


def _installed_versions(config: Config, engine: Engine | None) -> dict[str, str]:
    distributions = ["pypdfium2", "rapidocr", "wordninja"]
    engine_name = getattr(engine, "name", config.engine)
    if engine_name == "docling":
        distributions.append("docling")
    if any((config.describe_figures, config.ocr_page_vlm,
            config.digitize_vlm, config.figure_labels)):
        distributions.append("openai")
    if config.transcribe_equations:
        distributions.extend(("surya-ocr", "transformers"))
    versions = {}
    for name in distributions:
        try:
            versions[name] = package_version(name)
        except PackageNotFoundError:
            versions[name] = "not-installed"
    if config.table_ocr_executable:
        executable = shutil.which(config.table_ocr_executable)
        if executable is None:
            versions["tesseract"] = "not-found"
        else:
            completed = subprocess.run(
                [executable, "--version"], capture_output=True, text=True, check=False,
                timeout=10,
            )
            versions["tesseract"] = (
                completed.stdout.splitlines()[0] if completed.returncode == 0 else "unavailable"
            )
    return versions


@cache
def _implementation_sha256() -> str:
    """Fingerprint the installed pdf2md Python implementation, not only its version."""
    package_root = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(package_root.rglob("*.py")):
        digest.update(path.relative_to(package_root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _engine_identity(engine: Engine | None, config: Config) -> dict[str, str]:
    if engine is None:
        if config.engine == "mineru":
            from pdf2md.engines.mineru import MinerUEngine

            candidate = MinerUEngine(
                config.mineru_executable, deskew_scans=config.deskew_scans
            )
            return {
                "name": candidate.name,
                "implementation": "pdf2md.engines.mineru.MinerUEngine",
                "cache_identity": candidate.cache_identity(),
            }
        return {
            "name": "docling",
            "implementation": "pdf2md.engines.docling.DoclingEngine",
        }
    cls = type(engine)
    identity = {
        "name": getattr(engine, "name", cls.__name__),
        "implementation": f"{cls.__module__}.{cls.__qualname__}",
    }
    custom = getattr(engine, "cache_identity", None)
    if custom is not None:
        identity["cache_identity"] = str(custom() if callable(custom) else custom)
    return identity


def _run_inputs(source_sha256: str, config: Config, engine: Engine | None) -> dict:
    vision_enabled = any((config.describe_figures, config.ocr_page_vlm,
                          config.digitize_vlm, config.figure_labels))
    return {
        "schema_version": _RUN_INPUTS_SCHEMA_VERSION,
        "source_sha256": source_sha256,
        "tool_version": __version__,
        "implementation_sha256": _implementation_sha256(),
        "format_version": FORMAT_VERSION,
        "engine": _engine_identity(engine, config),
        "dependency_versions": _installed_versions(config, engine),
        "effective_config": config.effective_dict(),
        "vision_cache_schema_version": VISION_CACHE_SCHEMA_VERSION if vision_enabled else None,
        "vision_prompt_sha256": VISION_PROMPT_SHA256 if vision_enabled else None,
    }


def _get_engine(engine: Engine | None, config: Config) -> Engine:
    if engine is not None:
        return engine
    if config.engine == "mineru":
        from pdf2md.engines.mineru import MinerUEngine

        return MinerUEngine(
            config.mineru_executable, deskew_scans=config.deskew_scans
        )
    from pdf2md.engines.docling import DoclingEngine

    return DoclingEngine(
        formula_enrichment=config.do_formula_enrichment,
        force_ocr=config.force_ocr,
        # --ocr-page-vlm transcribes every page itself, so skip Docling's OCR entirely (roughly
        # halves the run on a scanned book); layout/figure detection still runs, and _vlm_ocr_pages
        # enumerates the scanned pages straight from the PDF rather than from Docling's blocks.
        skip_ocr=config.ocr_page_vlm,
        artifacts_path=config.local_model_dir,
        device=config.device,
    )


def convert_file(
    pdf_path: Path,
    *,
    engine: Engine | None = None,
    transcriber: Transcriber | None = None,
    describer: Describer | None = None,
    config: Config | None = None,
    force: bool = False,
    output_root: Path | None = None,
) -> ConvertResult:
    pdf_path = Path(pdf_path)
    config = config or Config()
    progress = Progress(log)
    progress.stage("preparing %s", pdf_path.name)
    doc_id = content_hash(pdf_path)
    dd = doc_dir(doc_id, pdf_path, root=output_root)
    _store_source(pdf_path, dd, doc_id)
    run_inputs = _run_inputs(doc_id, config, engine)
    fingerprint = run_fingerprint(run_inputs)

    cached = matching_version(dd, fingerprint)
    if cached is not None and not force:
        vdir = dd / f"v{cached}"
        prov = vdir / "provenance.json"
        stored = json.loads(prov.read_text()) if prov.exists() else {}
        pages = stored.get("page_count", 0)
        stored_metrics = (stored.get("provenance") or {}).get("run_metrics", {})
        log.info("cached: %s (v%d, run %s)", pdf_path.name, cached, fingerprint[:12])
        return ConvertResult(
            doc_id,
            cached,
            vdir,
            sorted(vdir.glob("*.md")),
            page_count=pages,
            cached=True,
            run_metrics=stored_metrics,
        )
    if not force:
        partial = matching_version(dd, fingerprint, include_partial=True)
        if partial is not None:
            partial_path = dd / f"v{partial}" / "provenance.json"
            partial_document = json.loads(partial_path.read_text())
            partial_metrics = (
                (partial_document.get("provenance") or {}).get("run_metrics", {})
            )
            log.info(
                "retrying %s: matching v%d has %d failed optional model call(s); "
                "completed regions remain cached",
                pdf_path.name,
                partial,
                failed_optional_calls(partial_metrics),
            )

    # Build the optional vision client up front (cheap, no network) so a vision flag
    # without the extra fails here, before the engine runs and writes a
    # partial dir.
    if (config.describe_figures or config.ocr_page_vlm or config.digitize_vlm
            or config.figure_labels) and describer is None:
        describer = get_describer(config)

    started = datetime.now(timezone.utc)
    metrics = RunMetrics()
    vision_cache_stats = CacheStats()
    try:
        engine = _get_engine(engine, config)
    except Exception as exc:  # noqa: BLE001 - report setup failures like document failures
        log.error("engine setup failed for %s: %s", pdf_path.name, exc)
        return ConvertResult(doc_id, 0, dd, [], failed=True, error=str(exc))
    engine_name = getattr(engine, "name", type(engine).__name__)
    source_pages = _source_page_count(pdf_path)
    metrics.finish("setup", source_pages=source_pages)
    if (
        source_pages is not None
        and source_pages >= 200
        and config.do_formula_enrichment
        and engine_name != "stored"
    ):
        log.warning(
            "preflight: %d-page document with formula enrichment enabled; "
            "this stage can take hours on equation-heavy books. Use --no-formula "
            "for a faster image-backed base bundle, then run `pdf2md enrich ... --equations`",
            source_pages,
        )
    if source_pages is None:
        progress.stage("reading source with %s", engine_name)
        heartbeat = f"still reading source with {engine_name}"
    else:
        progress.stage(
            "reading %d-page source with %s; engine reports again when complete",
            source_pages,
            engine_name,
        )
        heartbeat = (
            f"still reading {source_pages}-page source with {engine_name}; "
            "per-page progress unavailable"
        )
    try:
        with collapse_repeated_warnings(
            _OCR_LOGGERS,
            report_to=log,
            stage="source read",
        ) as engine_warnings:
            if engine_name == "mineru":
                result = engine.convert(pdf_path)
            else:
                with progress.heartbeat(heartbeat):
                    result = engine.convert(pdf_path)
    except Exception as exc:  # noqa: BLE001 - document-level isolate-and-flag
        log.error("engine failed on %s: %s", pdf_path.name, exc)
        return ConvertResult(doc_id, 0, dd, [], failed=True, error=str(exc))
    if engine.name != "stored":
        # A stored engine replays state that was normalized before it was written
        # (a pre-0.13 bundle predates the shift; reconvert those, don't re-shift).
        normalize_page_origin(result, pdf_path)
    progress.stage(
        "source read complete: %d pages, %d blocks, %d tables, %d figures",
        len(result.page_sizes), len(result.blocks), len(result.tables), len(result.figures),
    )
    metrics.finish(
        "parse",
        pages=len(result.page_sizes),
        blocks=len(result.blocks),
        tables=len(result.tables),
        figures=len(result.figures),
        third_party_warning_types=len(engine_warnings.counts),
        third_party_warning_repeats=engine_warnings.repeat_count,
    )

    version = next_version(dd)
    vdir = dd / f"v{version}"
    # A crashed earlier run can leave this version's dir (it had no provenance.json, so
    # next_version reuses the number); clear it so stale state and artifacts do not survive.
    if vdir.exists():
        shutil.rmtree(vdir)
    write_engine_state(vdir, doc_id, result)

    figure_cleanup = clean_figure_structure(result.blocks, result.figures)
    if any(figure_cleanup.values()):
        progress.stage(
            "figure cleanup: %d journal-furniture item(s) removed, %d panel(s) merged, "
            "%d continuation fragment(s) merged, %d graphical-abstract component(s) "
            "included, %d clipped heading(s) restored",
            figure_cleanup["furniture_removed"],
            figure_cleanup["panels_merged"],
            figure_cleanup["fragments_merged"],
            figure_cleanup["graphic_components_included"],
            figure_cleanup["panel_headings_absorbed"],
        )

    # Engine-agnostic verification layer (scripts, ligatures, equation cross-check,
    # OCR detection), off the engine so any backend inherits it.
    if config.detect_scripts:
        progress.stage("checking text and table geometry")
        with GlyphIndex(pdf_path, force_ocr=config.force_ocr) as glyphs:
            enrich_blocks(result.blocks, glyphs)
            enrich_tables(result.tables, result.raw_tables, glyphs)
            enrich_figures(result.figures, glyphs)
            # After the table pass: a block that renders from cells has no text of
            # its own to measure until its markup is final.
            record_recall(result.blocks, result.tables, glyphs)

    figure_cleanup["captions_associated"] = associate_figure_captions(
        result.blocks, result.figures
    )

    # Clean up OCR'd scanned prose (the `ocr` flag is set above): comma spacing always
    # (language-agnostic), English word-split only when enabled. Born-digital text is untouched.
    resegment_ocr_prose(result.blocks, word_split=config.resegment_ocr)

    # Renderer-coverage evidence on every equation's LaTeX (cheap parse, no drawing;
    # silently skipped when matplotlib isn't installed).
    from pdf2md.confidence import check_equation_render_support

    check_equation_render_support(result.blocks)

    # Transcribe each scanned page whole with the vision model — the model sees the full
    # layout/reading-order/tables at once, so a scanned page's prose blocks are replaced by one
    # accurate transcription (figures still crop). Page-level, before structure reads the blocks.
    if config.ocr_page_vlm and describer is not None:
        result.blocks = _vlm_ocr_pages(
            result.blocks,
            describer,
            pdf_path,
            config,
            dd,
            cache_stats=vision_cache_stats,
        )

    bookmarks = read_bookmarks(pdf_path)
    meta = extract_metadata(pdf_path, result.blocks, bookmarks)
    doi_metadata: dict | None = None
    if config.doi_metadata and meta.get("doi"):
        progress.stage("enriching metadata from DOI registry")
        doi_metadata = fetch_doi_metadata(
            meta["doi"],
            timeout=config.doi_metadata_timeout,
        )
        if doi_metadata is not None:
            meta = merge_doi_metadata(meta, doi_metadata)
    grobid_tei: dict[str, bytes] | None = None
    grobid_references: list[dict] | None = None
    if config.grobid_url:
        from pdf2md.grobid import HEADER_TEI_NAME, REFS_TEI_NAME, fetch_grobid, merge_grobid

        progress.stage("enriching metadata with GROBID")
        enriched = fetch_grobid(pdf_path, config.grobid_url,
                                timeout=config.grobid_timeout)
        if enriched is not None:
            meta = merge_grobid(meta, enriched["header"])
            meta["references_count"] = len(enriched["references"])
            grobid_references = enriched["references"]
            grobid_tei = enriched["tei"]
            log.info(
                "GROBID: %d reference(s), title=%s",
                len(enriched["references"]),
                (meta.get("title") or "")[:60],
            )
    progress.stage("building document structure")
    structure = build_structure(
        result.blocks,
        bookmarks,
        title=meta.get("title") or pdf_path.stem,
        page_count=len(result.page_sizes),
    )
    cache_checkpoint = vision_cache_stats.snapshot()
    metrics.finish(
        "geometry",
        scripts_enabled=config.detect_scripts,
        **{f"vision_cache_{name}": value for name, value in cache_checkpoint.items()},
        **figure_cleanup,
    )

    assets = vdir / "assets"
    data_dir = vdir / "data"

    table_blocks, authoritative_tables = _table_crops(
        result.blocks,
        result.tables,
        include_structured=bool(config.table_ocr_executable),
    )
    crop_blocks = _eq_crops(result.blocks) + table_blocks
    crop_count = sum(figure.bbox is not None for figure in result.figures) + len(crop_blocks)
    if crop_count:
        progress.stage("rendering %d source crops", crop_count)
    _render_crops(pdf_path, result.figures, crop_blocks, assets, config)
    _attach_table_crops(result.blocks, result.tables, authoritative_tables)
    _audit_scanned_tables(result.tables, vdir)

    ocr_pages = {b.page for b in result.blocks if b.extra.get("ocr")}
    _warn_about_scan_overlays(pdf_path, ocr_pages, config)

    # Lossless vector export beside the PNG crop (--figure-svg): a born-digital figure's
    # geometry and text as SVG a reader can parse. Scanned pages skip — their SVG would
    # just wrap the raster.
    if config.figure_svg:
        _svg_figures(result.figures, ocr_pages, pdf_path, assets)

    # Verification rasters for scanned pages: their OCR text isn't authoritative, so the
    # page image is — linked from each page anchor so prose can be checked, not just crops.
    page_rasters: dict[int, str] = {}
    if config.page_images:
        pages = (
            set(range(1, len(result.page_sizes) + 1))
            if config.page_images_all_pages else ocr_pages
        )
        if pages:
            progress.stage("rendering %d page images", len(pages))
        page_rasters = _render_pages(pdf_path, pages, assets, config)

    # Re-OCR each scanned figure's crop upright. The engine reads a sideways scan's small text
    # (axis ticks, titles) as garbage; a derotated re-read recovers it clean. Model-free;
    # born-digital figures aren't scanned, so they keep their exact text-layer labels.
    if config.ocr_figures:
        _ocr_scanned_figures(result.figures, ocr_pages, vdir)
    metrics.finish(
        "render",
        crops_requested=crop_count,
        page_images=len(page_rasters),
        figure_svg_enabled=config.figure_svg,
        figure_ocr_enabled=config.ocr_figures,
    )

    # Multi-pass: re-transcribe each image-backed equation with a local math-OCR
    # model so its hint beats the engine's garbled/OCR LaTeX. The crop stays the
    # authoritative source, so this only ever improves the rendering beside it.
    if config.transcribe_equations:
        transcriber = transcriber or get_transcriber(config)
        if transcriber is not None:
            _transcribe_equations(
                result.blocks,
                transcriber,
                vdir,
                dd,
                cache_stats=vision_cache_stats,
            )

    # Render-back evidence: draw each image-backed equation's LaTeX and compare
    # ink layout against its crop (opt-in; matplotlib). Verdict tiers only.
    if config.check_equation_render:
        from pdf2md.confidence import check_equation_renders

        progress.stage("render-checking equation crops")
        check_equation_renders(result.blocks, version_dir=vdir)
    metrics.finish(
        "equations",
        equations=sum(block.type == BlockType.EQUATION for block in result.blocks),
        transcription_enabled=config.transcribe_equations,
        render_check_enabled=config.check_equation_render,
    )

    # Recover plotted data from born-digital vector charts (near-lossless, no model, on by
    # default). Raster/scanned figures yield nothing at tier 1 and stay crops; --digitize-vlm
    # adds a model estimate for those. The OR keeps the pass running if only the VLM tier is on.
    chart_counts = {
        "attempted": 0,
        "accepted": 0,
        "declined": 0,
        "failed": 0,
        "ocr_axis_attempted": 0,
        "ocr_axis_ineligible": 0,
    }
    with collapse_repeated_warnings(
        _OCR_LOGGERS,
        report_to=log,
        stage="chart digitization",
    ) as chart_warnings:
        if config.digitize_figures or config.digitize_vlm:
            chart_counts = _digitize_figures(
                result.figures,
                pdf_path,
                config,
                describer,
                vdir,
                progress=progress,
                cache_stats=vision_cache_stats,
            )
    chart_cache = vision_cache_stats.since(cache_checkpoint)
    metrics.finish(
        "charts",
        enabled=config.digitize_figures or config.digitize_vlm,
        third_party_warning_types=len(chart_warnings.counts),
        third_party_warning_repeats=chart_warnings.repeat_count,
        **{f"vision_cache_{name}": value for name, value in chart_cache.items()},
        **chart_counts,
    )
    cache_checkpoint = vision_cache_stats.snapshot()

    # Read the printed labels off each figure (axis titles, peak/data labels, legend) —
    # OCR of what's written, reliable where curve digitization can't be. Crop stays source.
    if config.figure_labels and describer is not None:
        _label_figures(
            result.figures,
            describer,
            config,
            vdir,
            dd,
            pdf_path,
            cache_stats=vision_cache_stats,
        )

    # Lift a figure's caption out of its recovered labels into the caption field, so it renders
    # as the figure's own caption (visible, searchable) instead of buried in the label list.
    _promote_figure_captions(result.figures)

    # Describe each crop (figure, image-fallback table, image-backed equation) with a
    # vision model so the opaque PNG carries a text aid. The crop stays authoritative.
    if config.describe_figures:
        describer = describer or get_describer(config)
        if describer is not None:
            _describe_crops(
                result.figures,
                result.blocks,
                describer,
                vdir,
                config,
                cache_stats=vision_cache_stats,
            )

    # A degraded vision run (endpoint dropped connections under load) must not pass as a
    # clean conversion — the OCR text fell back to the engine and crops have no aid.
    if describer is not None and describer.failures:
        log.warning("%d/%d vision calls failed (endpoint errors) after retries; "
                    "descriptions/OCR are incomplete. Check the endpoint, then rerun the "
                    "same command; completed regions remain cached",
                    describer.failures, describer.calls)
    description_cache = vision_cache_stats.since(cache_checkpoint)
    metrics.finish(
        "descriptions",
        figure_labels_enabled=config.figure_labels,
        crop_descriptions_enabled=config.describe_figures,
        vision_calls=getattr(describer, "calls", 0) if describer is not None else 0,
        vision_failures=getattr(describer, "failures", 0) if describer is not None else 0,
        **{f"vision_cache_{name}": value for name, value in description_cache.items()},
    )

    doc = Document(
        doc_id=doc_id,
        source_path=str(pdf_path),
        source_sha256=doc_id,
        version=version,
        page_count=len(result.page_sizes),
        sections=structure.root,
        blocks=result.blocks,
        tables=result.tables,
        figures=result.figures,
    )
    document_metadata = build_document_metadata(
        doc,
        meta,
        section_source=structure.section_source,
        grobid_references=grobid_references,
    )
    write_document_metadata(vdir, document_metadata)
    meta["document_type"] = document_metadata["document"]["kind"]["value"]
    meta["metadata_artifact"] = "metadata.json"
    section_roles = {
        section["section_id"]: section["semantic_role"]
        for section in document_metadata["sections"]
    }
    progress.stage("writing Markdown and table artifacts")
    if doi_metadata is not None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (vdir / DOI_METADATA_NAME).write_text(
            json.dumps(doi_metadata, indent=2, ensure_ascii=False) + "\n"
        )
    if grobid_tei:
        data_dir.mkdir(parents=True, exist_ok=True)
        for name, xml_bytes in grobid_tei.items():
            if xml_bytes:
                (vdir / name).write_bytes(xml_bytes)
    emission_index: dict[str, dict] = {}
    md_files, flags = emit_document(
        doc,
        structure,
        vdir,
        meta,
        result.engine_versions,
        page_rasters=page_rasters,
        table_ocr_executable=config.table_ocr_executable,
        table_reference_path=config.table_reference_path,
        progress=progress,
        formula_enrichment_enabled=config.do_formula_enrichment,
        emission_index=emission_index,
    )
    doc.coverage = build_report(doc_id, result.blocks, flags)
    metrics.finish(
        "emit",
        markdown_files=len(md_files),
        emitted=doc.coverage.emitted,
        cropped=doc.coverage.cropped,
        flagged=doc.coverage.flagged,
        dropped=doc.coverage.dropped,
    )

    # Per-doc profile, surfaced for an AI (profile.json) and a human (README.md).
    # The numeric-conservation pass reads the embedded layer once more (read-only)
    # against the markdown just written; word recall was recorded during enrichment.
    progress.stage("writing profile, chunks, and manifest")
    consistency = numeric_conservation(
        pdf_path,
        (path.read_text() for path in md_files),
        force_ocr=config.force_ocr,
        document=doc,
        emission_index=emission_index,
    )
    conservation_flags = conservation_review_flags(consistency)
    recall_flags, diacritic_flags = recall_review_flags(result.blocks)
    order_flags, order_pages = reading_order_flags(
        result.blocks, emission_index, pdf_path=pdf_path, force_ocr=config.force_ocr
    )
    doc.coverage.flags.extend(
        conservation_flags + recall_flags + order_flags + diacritic_flags
    )
    # Diacritic findings reach review.json and profile.json but not the Markdown:
    # the content is there and mis-spelled, and one marker per accented surname
    # would bury a bibliography.
    annotate_conservation_warnings(
        vdir,
        conservation_flags + _placeable(recall_flags + order_flags, emission_index),
        emission_index,
    )
    annotate_table_artifacts(vdir, doc, doc.coverage.flags)
    review_queue = build_review_queue(doc)
    profile = build_profile(
        doc,
        consistency=consistency,
        metadata=meta,
        engine_quality=result.quality_evidence,
        review_queue=review_queue,
        reading_order=order_pages,
    )
    write_review_files(vdir, review_queue)
    write_profile(vdir, doc, profile, md_files)
    chunks_path = write_chunks(
        vdir,
        doc,
        md_files,
        page_rasters,
        emission_index=emission_index,
    )
    passage_tokenizer = load_passage_tokenizer(config.passage_tokenizer)
    passages_path, _, passage_count = write_passages(
        vdir,
        doc,
        meta,
        md_files,
        page_rasters,
        emission_index=emission_index,
        tokenizer=passage_tokenizer,
        max_tokens=config.passage_max_tokens,
        section_roles=section_roles,
    )
    write_document_map(
        vdir,
        doc,
        meta,
        md_files,
        passages_path,
        section_roles=section_roles,
    )
    write_symbol_index(vdir, doc.doc_id, passages_path)
    write_manifest(
        vdir, doc, meta, profile, md_files, page_rasters,
        review_queue=review_queue,
        passage_count=passage_count,
        document_metadata=document_metadata,
    )
    with chunks_path.open() as chunks_file:
        chunk_count = sum(1 for _ in chunks_file)
    metrics.finish(
        "audit",
        review_flags=profile.review_flags,
        action_required=profile.review_counts.get("action_required", 0),
        source_dependent=profile.review_counts.get("source_dependent", 0),
        informational=profile.review_counts.get("informational", 0),
        chunks=chunk_count,
        passages=passage_count,
    )

    linked_assets, linked_bytes = deduplicate_assets(vdir)
    if linked_assets:
        log.info(
            "deduplicated %d unchanged asset(s), %d logical bytes",
            linked_assets,
            linked_bytes,
        )
    metrics.finish(
        "finalize",
        deduplicated_assets=linked_assets,
        deduplicated_bytes=linked_bytes,
    )
    run_metrics = metrics.report()
    write_readme(
        vdir,
        doc,
        meta,
        profile,
        md_files,
        run_metrics=run_metrics,
        passage_count=passage_count,
    )

    finished = datetime.now(timezone.utc)
    doc.provenance = Provenance(
        tool_version=__version__,
        engine_versions=result.engine_versions,
        format_version=FORMAT_VERSION,
        source_path=str(pdf_path),
        source_sha256=doc_id,
        page_count=doc.page_count,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        duration_s=run_metrics["duration_s"],
        section_source=structure.section_source,
        derivation=getattr(engine, "derivation", {"kind": "base"}),
        run_fingerprint=fingerprint,
        run_inputs=run_inputs,
        run_metrics=run_metrics,
    )
    # provenance.json is the completion marker (its presence = a finished run), so write
    # it atomically — a truncated marker from a killed process must never look complete.
    prov_path = vdir / "provenance.json"
    tmp_prov = prov_path.with_suffix(".json.tmp")
    tmp_prov.write_text(json.dumps(doc.to_dict(), indent=2, default=str))
    tmp_prov.replace(prov_path)

    log.info(
        "converted %s -> v%d (%d md files, %s)",
        pdf_path.name, version, len(md_files),
        "INCOMPLETE ACCOUNTING" if not doc.coverage.accounted_for else
        "review required" if doc.coverage.needs_review else "complete",
    )
    return ConvertResult(
        doc_id,
        version,
        vdir,
        md_files,
        coverage=doc.coverage,
        page_count=doc.page_count,
        run_metrics=run_metrics,
    )


def _source_page_count(pdf_path: Path) -> int | None:
    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
    except Exception:  # noqa: BLE001 - the engine reports the useful parse error later
        return None
    try:
        return len(pdf)
    finally:
        pdf.close()


def convert_dir(
    root: Path,
    *,
    engine: Engine | None = None,
    config: Config | None = None,
    force: bool = False,
) -> list[ConvertResult]:
    root = Path(root).expanduser().resolve()
    output = out_root()
    nested_output = output != root and output.is_relative_to(root)
    pdfs = sorted(
        pdf for pdf in root.rglob("*.pdf")
        if not (nested_output and pdf.resolve().is_relative_to(output))
        and not (pdf.name == "source.pdf" and is_document_dir(pdf.parent))
    )
    if not pdfs:
        log.warning("no PDFs under %s", root)
        return []
    config = config or Config()
    try:
        engine = _get_engine(engine, config)  # build once, reuse across the batch
        transcriber = get_transcriber(config)  # loads the math-OCR model once, if enabled
        describer = get_describer(config)      # one vision client, reused across the batch
    except Exception as exc:  # noqa: BLE001 - report setup failures for every input
        log.error("batch setup failed under %s: %s", root, exc)
        return [
            ConvertResult(pdf.name, 0, root, [], failed=True, error=str(exc))
            for pdf in pdfs
        ]
    results: list[ConvertResult] = []
    progress = Progress(log)
    progress.count("converting PDFs", 0, len(pdfs), unit="PDFs", force=True)
    for completed, pdf in enumerate(pdfs, start=1):
        try:
            results.append(convert_file(
                pdf, engine=engine, transcriber=transcriber, describer=describer,
                config=config, force=force))
        except Exception as exc:  # noqa: BLE001 - poison-pill isolation
            log.error("unhandled failure on %s: %s", pdf.name, exc)
            # Don't re-hash here — if the file is unreadable, that throws too and aborts
            # the batch this handler exists to protect. The name is enough to report it.
            results.append(
                ConvertResult(pdf.name, 0, root, [], failed=True, error=str(exc))
            )
        progress.count(
            "converting PDFs", completed, len(pdfs), unit="PDFs", detail=pdf.name,
            force=True,
        )
    return results


def _eq_crops(blocks) -> list:
    """Equations whose text is unreliable or absent, so the image crop is the faithful source:
    a low-confidence reading (garbled/scrambled LaTeX), or no text at all — the `--no-formula`
    case, where the region was never transcribed and would otherwise render as an empty marker
    with its content lost. Either way the crop, not the text, is authoritative. In formula-on
    runs every equation has text, so only the low-confidence clause fires there."""
    return [
        b for b in blocks
        if b.type is BlockType.EQUATION and b.bbox is not None
        and ((b.confidence is not None and b.confidence < RECOVER_BELOW) or not b.text.strip())
    ]


def _transcribe_equations(
    blocks,
    transcriber,
    vdir: Path,
    document_dir: Path | None = None,
    *,
    cache_stats: CacheStats | None = None,
) -> None:
    """Store a better hint on each image-backed equation from re-OCR'ing its crop."""
    cache = load_vision_cache(document_dir or vdir.parent, cache_stats)
    custom_identity = getattr(transcriber, "cache_identity", None)
    identity = (
        str(custom_identity() if callable(custom_identity) else custom_identity)
        if custom_identity is not None else
        f"{type(transcriber).__module__}.{type(transcriber).__qualname__}"
    )
    for b in blocks:
        crop = b.extra.get("crop_path")
        if b.type is BlockType.EQUATION and crop:
            image_path = vdir / crop
            if not image_path.is_file():
                latex = transcriber.transcribe(image_path)
                if latex:
                    b.extra["transcribed"] = latex
                    b.extra["transcribed_source"] = "math OCR"
                continue
            key_payload = {
                "schema": 1,
                "kind": "equation-transcription",
                "transcriber": identity,
                "image_sha256": content_hash(image_path),
                "implementation_sha256": _implementation_sha256(),
            }
            key = "transcription-v1:" + hashlib.sha256(
                json.dumps(key_payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            latex = cache.get(key)
            if latex is None:
                latex = transcriber.transcribe(image_path)
                if latex:
                    cache[key] = latex
            if latex:
                b.extra["transcribed"] = latex
                b.extra["transcribed_source"] = "math OCR"


def _placeable(flags, emission_index: dict[str, dict]) -> list:
    """Flags whose block has somewhere in the Markdown to put a marker.

    A block can be measured and still have no span: a title heading is consumed
    into the front matter rather than emitted as body. Conservation flags are
    deliberately not filtered here — a conservation finding on an unplaced block
    is a contradiction the annotation pass should raise on."""
    return [
        flag for flag in flags
        if (entry := emission_index.get(flag.block_id))
        and entry.get("start") is not None
        and entry.get("markdown")
    ]


def _table_crops(blocks, tables, *, include_structured: bool = False) -> tuple[list, set]:
    """Every table block gets a crop of its own region, so a reader can check the
    printed table without opening the PDF. The returned id set is the subset
    where that image is *authoritative* rather than a reference: tables Docling
    failed to parse into cells (kept a bbox but no renderable content, would
    otherwise drop), tables on an OCR'd scan page (the cells are OCR guesses, so
    the scan pixels are the ground truth), and tables whose cells the glyph check
    found unbacked (a vision model read a raster table — no embedded text exists
    behind the cells). `include_structured` adds every table to that set for
    --table-ocr, whose independent reader reads the crop."""
    rendered = {t.block_id for t in tables if (t.gfm or "").strip() or t.html}
    unbacked = glyph_unbacked_tables(tables)
    selected, authoritative = [], set()
    for b in blocks:
        # Key on type, not the `#/tables/` id prefix: --ocr-page-vlm repurposes a table block's
        # id into the page-transcription paragraph, which must NOT be cropped as a table.
        if b.bbox is None or b.type is not BlockType.TABLE:
            continue
        selected.append(b)
        if include_structured or b.id not in rendered or b.extra.get("ocr"):
            authoritative.add(b.id)
        elif b.id in unbacked:
            b.extra["cells_unverified"] = True
            authoritative.add(b.id)
    return selected, authoritative


def _warn_about_scan_overlays(pdf_path: Path, ocr_pages: set[int], config: Config) -> None:
    """Say so when a document is a scan carrying someone else's OCR.

    Detecting it gets the posture right — crops authoritative, cells candidates —
    but the transcription is still whoever digitised the paper, and on an old
    scan that is the worst reading available. Measured over all 99 pages of a
    1972 data-table paper: the embedded layer leaves 22.9% of value tokens
    malformed and recovers 21% of each page's printed row grid, where MinerU
    leaves 0.6% and recovers 99%. A re-OCR through --force-ocr sits between them
    (8% on a three-page sample). Naming the better path is the point of the
    warning."""
    if not ocr_pages or config.force_ocr:
        return
    with GlyphIndex(pdf_path) as glyphs:
        overlaid = sum(glyphs.scanned_overlay(page) for page in sorted(ocr_pages))
    if overlaid:
        log.warning(
            "%d page(s) are scans carrying an embedded OCR text layer; that text is "
            "kept as a candidate beside the authoritative crops and is only as good "
            "as whoever digitised the paper. For a fresh transcription re-run with "
            "--engine mineru, which on a measured 1972 scan cut malformed value "
            "tokens from 22.9%% to 0.6%%; --force-ocr is the fallback where MinerU "
            "is unavailable.",
            overlaid,
        )


def _audit_scanned_tables(tables, version_dir: Path) -> None:
    """Row accounting for the tables the glyph path could not reach.

    Runs here rather than in `enrich_tables` because it needs the rendered crop,
    which does not exist until the crop stage. Only fills in where the glyph
    audit produced no row accounting at all -- a page with a text layer is
    already measured more precisely than pixels can manage."""
    for table in tables:
        if table.grid_audit.get("rows") or not table.source_crop:
            continue
        rows = len(gfm_rows(table.gfm)) if (table.gfm or "").strip() else 0
        if rows < 2:
            continue
        found = raster_row_findings(version_dir / table.source_crop, rows)
        if not found:
            continue
        table.grid_audit = {
            **table.grid_audit,
            **{k: v for k, v in found.items() if k != "findings"},
        }
        if found.get("findings"):
            table.grid_audit["findings"] = [
                *table.grid_audit.get("findings", []), *found["findings"],
            ]


def _attach_table_crops(blocks, tables, authoritative: set) -> None:
    """Hand each table its own crop, and keep `crop_path` for the tables whose
    image is the authority. That key is what tells the emitter to publish the
    image instead of the cells, and what marks the block source-dependent for
    conservation and passages — a usable grid must not carry it."""
    crops = {
        b.id: b.extra["crop_path"] for b in blocks
        if b.type is BlockType.TABLE and b.extra.get("crop_path")
    }
    for table in tables:
        table.source_crop = crops.get(table.block_id, "")
    for b in blocks:
        if b.type is BlockType.TABLE and b.id not in authoritative:
            b.extra.pop("crop_path", None)


def _render_pages(pdf_path: Path, pages: set[int], assets: Path, config: Config) -> dict[int, str]:
    """Render each scanned page to assets/page_NNN.png; returns page -> asset relpath."""
    if not pages:
        return {}
    rasters: dict[int, str] = {}
    with CropRenderer(pdf_path, dpi=config.page_image_dpi) as cr:
        for p in sorted(pages):
            name = f"page_{p:03d}.png"
            try:
                cr.full_page(p, assets / name)
            except Exception as exc:  # noqa: BLE001 - one bad page shouldn't abort the run
                log.warning("page raster failed for page %d: %s", p, exc)
                continue
            rasters[p] = f"assets/{name}"
    return rasters


def _render_crops(pdf_path: Path, figures, eq_blocks, assets: Path, config: Config) -> None:
    if not figures and not eq_blocks:
        return
    with CropRenderer(pdf_path, dpi=config.crop_dpi, padding_pts=config.crop_padding_pts) as cr:
        for fig in figures:
            if fig.bbox is None:
                continue
            name = f"{fig.block_id.strip('#/').replace('/', '_')}_p{fig.page}.png"
            try:
                cr.crop(fig.page, fig.bbox, assets / name,
                        dpi=dpi_for_region(fig.bbox, config.figure_crop_target_px, config.crop_dpi))
                fig.asset_path = f"assets/{name}"
            except Exception as exc:  # noqa: BLE001 - page-level isolate-and-flag
                log.warning("crop failed for %s: %s", fig.block_id, exc)
        for b in eq_blocks:
            name = f"{b.id.strip('#/').replace('/', '_')}_p{b.page}.png"
            try:
                cr.crop(b.page, b.bbox, assets / name,
                        dpi=_block_crop_dpi(b, config))
                b.extra["crop_path"] = f"assets/{name}"
            except Exception as exc:  # noqa: BLE001 - page-level isolate-and-flag
                log.warning("equation crop failed for %s: %s", b.id, exc)


def _block_crop_dpi(block: Block, config: Config) -> int:
    floor = max(config.crop_dpi, config.scan_crop_dpi) if block.extra.get("ocr") else config.crop_dpi
    return dpi_for_region(block.bbox, config.vlm_crop_target_px, floor)
