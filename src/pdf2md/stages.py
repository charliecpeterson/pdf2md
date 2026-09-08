"""The stages of a conversion, and the state they hand along.

`pipeline.convert_file` is the list of these in order. Each takes the one `_Run`
that carries what the stages share and mutates it, because extracted plainly they
take fifteen arguments and read worse than the inline code they replaced. The
order is load-bearing in three places, each marked where it matters: the glyph
verification runs after the table pass, the page-level VLM transcription runs
before `build_structure` consumes the block list, and the vision-cache checkpoint
is taken where the geometry metrics are recorded, which is what the charts stage's
cache numbers are measured against.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pypdfium2 as pdfium

from pdf2md.bookmarks import read_bookmarks
from pdf2md.cache import content_hash, doc_dir, matching_version, next_version, run_fingerprint
from pdf2md.config import Config
from pdf2md.coverage import build_report
from pdf2md.crops import (
    _attach_table_crops,
    _eq_crops,
    _render_crops,
    _render_pages,
    _table_crops,
)
from pdf2md.describe import Describer, get_describer
from pdf2md.document_metadata import build_document_metadata, write_document_metadata
from pdf2md.doi_metadata import DOI_METADATA_NAME, fetch_doi_metadata, merge_doi_metadata
from pdf2md.emit import emit_document
from pdf2md.engine_state import write_engine_state
from pdf2md.engines.base import Engine, normalize_page_origin
from pdf2md.engines.select import select_engine
from pdf2md.enrich import (
    GlyphIndex,
    enrich_blocks,
    enrich_figures,
    enrich_tables,
    resegment_ocr_prose,
    warn_about_scan_overlays,
)
from pdf2md.figure_passes import (
    _describe_crops,
    _digitize_figures,
    _label_figures,
    _ocr_scanned_figures,
)
from pdf2md.logging import Progress, collapse_repeated_warnings, get_logger
from pdf2md.metadata import extract_metadata
from pdf2md.recall import record_recall
from pdf2md.run_identity import _run_inputs, _store_source
from pdf2md.run_metrics import RunMetrics, failed_optional_calls
from pdf2md.scan_ocr import _vlm_ocr_pages
from pdf2md.schema import BlockType, ConvertResult, Document
from pdf2md.structure import build_structure
from pdf2md.table_audit import audit_running_text_rows, audit_scanned_tables
from pdf2md.transcribe import Transcriber, get_transcriber, transcribe_equations
from pdf2md.vision_cache import CacheStats
from pdf2md.visual import (
    _promote_figure_captions,
    _svg_figures,
    associate_figure_captions,
    clean_figure_structure,
)

log = get_logger("pipeline")
_OCR_LOGGERS = ("RapidOCR", "docling.models.stages.ocr.rapid_ocr_model")


def _read_heartbeat(engine: Engine, engine_name: str, source_pages: int) -> object:
    """The message the source-read stage beats with, counting pages where it can.

    An engine that can say how far it has read gets a live count and a rate; one
    that cannot says so. This exists because an 1,085-page parse reported
    "per-page progress unavailable" for 10 hours 48 minutes while working
    correctly, and was nearly killed twice on the suspicion it had hung.
    """
    seen = getattr(engine, "pages_seen", None)
    if not callable(seen):
        return (f"still reading {source_pages}-page source with {engine_name}; "
                "this engine reports no page counter")
    started = time.monotonic()

    def message() -> str:
        done = seen()
        elapsed = time.monotonic() - started
        if not done:
            return (f"still reading {source_pages}-page source with {engine_name}; "
                    "no pages read yet (model load or first page)")
        if done >= source_pages:
            # The counter follows pages fed into the pipeline, so it reaches the
            # end while the last stages are still draining. Saying "0 min left"
            # there would be the old problem in miniature.
            return (f"all {source_pages} pages read with {engine_name}; "
                    f"finishing the last of them")
        rate = elapsed / done
        left = max(0.0, (source_pages - done) * rate)
        return (
            f"read {done}/{source_pages} pages with {engine_name} "
            f"({rate:.1f}s/page, ~{left / 60:.0f} min left; the count follows pages "
            f"into the pipeline, so the estimate runs ahead)"
        )

    return message


_LONG_DOCUMENT_PAGES = 200


def _warn_about_long_formula_run(
    source_pages: int | None, engine_name: str, config: Config
) -> None:
    """Say before the wait, not after it.

    Formula enrichment is the slowest stage by a wide margin -- measured at 3.4x
    the whole conversion over 28 papers -- and on a long book it is hours. The
    warning names the escape (`--no-formula` now, `pdf2md enrich` later), because
    an hour into a progress bar is not the moment to learn there was a choice.
    """
    if (
        source_pages is not None
        and source_pages >= _LONG_DOCUMENT_PAGES
        and config.do_formula_enrichment
        and engine_name != "stored"
    ):
        log.warning(
            "preflight: %d-page document with formula enrichment enabled; "
            "this stage can take hours on equation-heavy books. Use --no-formula "
            "for a faster image-backed base bundle, then run `pdf2md enrich ... --equations`",
            source_pages,
        )


def _reuse_completed_version(
    pdf_path: Path, dd: Path, doc_id: str, fingerprint: str, force: bool
) -> ConvertResult | None:
    """The completed version this run would reproduce, if there is one.

    A version is reused only when its run fingerprint matches — the effective
    config, this implementation, the engine identity, dependency versions, model
    identifiers and prompt schema. A *partial* match is not reused: it is
    reported and then re-run, so the optional model work that failed gets another
    attempt while the regions that completed stay cached.
    """
    if force:
        return None
    cached = matching_version(dd, fingerprint)
    if cached is not None:
        vdir = dd / f"v{cached}"
        prov = vdir / "provenance.json"
        stored = json.loads(prov.read_text()) if prov.exists() else {}
        log.info("cached: %s (v%d, run %s)", pdf_path.name, cached, fingerprint[:12])
        return ConvertResult(
            doc_id,
            cached,
            vdir,
            sorted(vdir.glob("*.md")),
            page_count=stored.get("page_count", 0),
            cached=True,
            run_metrics=(stored.get("provenance") or {}).get("run_metrics", {}),
        )
    partial = matching_version(dd, fingerprint, include_partial=True)
    if partial is not None:
        stored = json.loads((dd / f"v{partial}" / "provenance.json").read_text())
        log.info(
            "retrying %s: matching v%d has %d failed optional model call(s); "
            "completed regions remain cached",
            pdf_path.name,
            partial,
            failed_optional_calls((stored.get("provenance") or {}).get("run_metrics", {})),
        )
    return None


@dataclass
class _Run:
    """The state a conversion carries from one stage to the next.

    The stages of `convert_file` share about twenty locals, which is why that
    function resisted being split for so long: extracted plainly, the final stage
    takes fifteen arguments and reads worse than the inline code it replaced.
    Naming the state is what makes the split an improvement rather than a
    rearrangement -- every stage now takes one parameter and mutates it, and the
    body of `convert_file` is the list of stages.

    Fields above the divider are established before the first stage runs; the
    rest are what a stage leaves for its successors.
    """

    pdf_path: Path
    config: Config
    progress: Progress
    metrics: RunMetrics
    started: datetime
    doc_id: str
    document_dir: Path
    fingerprint: str
    run_inputs: dict
    engine: Engine
    result: object
    version: int
    vdir: Path
    describer: Describer | None = None
    transcriber: Transcriber | None = None
    vision_cache: CacheStats = field(default_factory=CacheStats)

    doc: Document | None = None
    structure: object = None
    meta: dict = field(default_factory=dict)
    bookmarks: list = field(default_factory=list)
    figure_cleanup: dict = field(default_factory=dict)
    doi_metadata: dict | None = None
    grobid_tei: dict[str, bytes] | None = None
    grobid_references: list[dict] | None = None
    ocr_pages: set[int] = field(default_factory=set)
    page_rasters: dict[int, str] = field(default_factory=dict)
    cache_checkpoint: dict = field(default_factory=dict)
    document_metadata: dict = field(default_factory=dict)
    section_roles: dict = field(default_factory=dict)
    md_files: list = field(default_factory=list)
    emission_index: dict = field(default_factory=dict)

    @property
    def derivation(self) -> dict:
        """How this bundle was produced -- a base conversion, or replayed state."""
        return getattr(self.engine, "derivation", {"kind": "base"})

    @property
    def assets(self) -> Path:
        return self.vdir / "assets"


def _open_run(
    pdf_path: Path,
    *,
    engine: Engine | None,
    transcriber: Transcriber | None,
    describer: Describer | None,
    config: Config,
    force: bool,
    output_root: Path | None,
    progress: Progress,
) -> _Run | ConvertResult:
    """Identify the source, read it with an engine, and open a version to write.

    Returns a `ConvertResult` instead of a `_Run` on the three outcomes that end
    the conversion here: a completed version this run would reproduce, an engine
    that would not start, and an engine that failed on the document.
    """
    progress.stage("preparing %s", pdf_path.name)
    doc_id = content_hash(pdf_path)
    dd = doc_dir(doc_id, pdf_path, root=output_root)
    _store_source(pdf_path, dd, doc_id)
    run_inputs = _run_inputs(doc_id, config, engine)
    fingerprint = run_fingerprint(run_inputs)

    reused = _reuse_completed_version(pdf_path, dd, doc_id, fingerprint, force)
    if reused is not None:
        return reused

    # Build the optional vision client up front (cheap, no network) so a vision flag
    # without the extra fails here, before the engine runs and writes a
    # partial dir.
    if (config.describe_figures or config.ocr_page_vlm or config.digitize_vlm
            or config.figure_labels) and describer is None:
        describer = get_describer(config)

    started = datetime.now(UTC)
    metrics = RunMetrics()
    try:
        engine = select_engine(engine, config, pdf_path)
    except Exception as exc:  # noqa: BLE001 - report setup failures like document failures
        log.error("engine setup failed for %s: %s", pdf_path.name, exc)
        return ConvertResult(doc_id, 0, dd, [], failed=True, error=str(exc))
    engine_name = getattr(engine, "name", type(engine).__name__)
    source_pages = _source_page_count(pdf_path)
    metrics.finish("setup", source_pages=source_pages)
    _warn_about_long_formula_run(source_pages, engine_name, config)
    if source_pages is None:
        progress.stage("reading source with %s", engine_name)
        heartbeat = f"still reading source with {engine_name}"
    else:
        progress.stage(
            "reading %d-page source with %s; engine reports again when complete",
            source_pages,
            engine_name,
        )
        heartbeat = _read_heartbeat(engine, engine_name, source_pages)
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
    return _Run(
        pdf_path=pdf_path,
        config=config,
        progress=progress,
        metrics=metrics,
        started=started,
        doc_id=doc_id,
        document_dir=dd,
        fingerprint=fingerprint,
        run_inputs=run_inputs,
        engine=engine,
        result=result,
        version=version,
        vdir=vdir,
        describer=describer,
        transcriber=transcriber,
    )


def _verify_text(run: _Run) -> None:
    """The engine-agnostic verification layer, off the engine so any backend inherits it."""
    result, config = run.result, run.config
    run.figure_cleanup = clean_figure_structure(result.blocks, result.figures)
    if any(run.figure_cleanup.values()):
        run.progress.stage(
            "figure cleanup: %d journal-furniture item(s) removed, %d panel(s) merged, "
            "%d continuation fragment(s) merged, %d graphical-abstract component(s) "
            "included, %d clipped heading(s) restored",
            run.figure_cleanup["furniture_removed"],
            run.figure_cleanup["panels_merged"],
            run.figure_cleanup["fragments_merged"],
            run.figure_cleanup["graphic_components_included"],
            run.figure_cleanup["panel_headings_absorbed"],
        )

    if config.detect_scripts:
        run.progress.stage("checking text and table geometry")
        with GlyphIndex(run.pdf_path, force_ocr=config.force_ocr) as glyphs:
            enrich_blocks(result.blocks, glyphs)
            enrich_tables(result.tables, result.raw_tables, glyphs)
            enrich_figures(result.figures, glyphs)
            # After the table pass: a block that renders from cells has no text of
            # its own to measure until its markup is final.
            record_recall(result.blocks, result.tables, glyphs)

    run.figure_cleanup["captions_associated"] = associate_figure_captions(
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
    if config.ocr_page_vlm and run.describer is not None:
        result.blocks = _vlm_ocr_pages(
            result.blocks,
            run.describer,
            run.pdf_path,
            config,
            run.document_dir,
            cache_stats=run.vision_cache,
        )


def _describe_document(run: _Run) -> None:
    """Bibliographic identity and the section tree the emitter writes from."""
    result, config, pdf_path = run.result, run.config, run.pdf_path
    run.bookmarks = read_bookmarks(pdf_path)
    run.meta = extract_metadata(pdf_path, result.blocks, run.bookmarks)
    if config.doi_metadata and run.meta.get("doi"):
        run.progress.stage("enriching metadata from DOI registry")
        run.doi_metadata = fetch_doi_metadata(
            run.meta["doi"],
            timeout=config.doi_metadata_timeout,
        )
        if run.doi_metadata is not None:
            run.meta = merge_doi_metadata(run.meta, run.doi_metadata)
    if config.grobid_url:
        from pdf2md.grobid import fetch_grobid, merge_grobid

        run.progress.stage("enriching metadata with GROBID")
        enriched = fetch_grobid(pdf_path, config.grobid_url, timeout=config.grobid_timeout)
        if enriched is not None:
            run.meta = merge_grobid(run.meta, enriched["header"])
            run.meta["references_count"] = len(enriched["references"])
            run.grobid_references = enriched["references"]
            run.grobid_tei = enriched["tei"]
            log.info(
                "GROBID: %d reference(s), title=%s",
                len(enriched["references"]),
                (run.meta.get("title") or "")[:60],
            )
    run.progress.stage("building document structure")
    run.structure = build_structure(
        result.blocks,
        run.bookmarks,
        title=run.meta.get("title") or pdf_path.stem,
        page_count=len(result.page_sizes),
    )
    # The charts stage reports its cache activity as the difference from here, so
    # the position of this snapshot is what the "charts" cache numbers mean. It
    # spans the render and equation stages too; that is the reading the recorded
    # metrics have always had, and moving it would silently change them.
    run.cache_checkpoint = run.vision_cache.snapshot()
    run.metrics.finish(
        "geometry",
        scripts_enabled=config.detect_scripts,
        **{f"vision_cache_{name}": value for name, value in run.cache_checkpoint.items()},
        **run.figure_cleanup,
    )


def _render_assets(run: _Run) -> None:
    """Source crops, table crops and their audits, page rasters, scanned-figure re-OCR."""
    result, config, pdf_path, vdir = run.result, run.config, run.pdf_path, run.vdir
    table_blocks, authoritative_tables = _table_crops(
        result.blocks,
        result.tables,
        include_structured=bool(config.table_ocr_executable),
    )
    crop_blocks = _eq_crops(result.blocks) + table_blocks
    crop_count = sum(figure.bbox is not None for figure in result.figures) + len(crop_blocks)
    if crop_count:
        run.progress.stage("rendering %d source crops", crop_count)
    _render_crops(pdf_path, result.figures, crop_blocks, run.assets, config)
    _attach_table_crops(result.blocks, result.tables, authoritative_tables)
    audit_scanned_tables(result.tables, vdir)
    audit_running_text_rows(result.tables)

    run.ocr_pages = {b.page for b in result.blocks if b.extra.get("ocr")}
    warn_about_scan_overlays(pdf_path, run.ocr_pages, config)

    # Lossless vector export beside the PNG crop (--figure-svg): a born-digital figure's
    # geometry and text as SVG a reader can parse. Scanned pages skip — their SVG would
    # just wrap the raster.
    if config.figure_svg:
        _svg_figures(result.figures, run.ocr_pages, pdf_path, run.assets)

    # Verification rasters for scanned pages: their OCR text isn't authoritative, so the
    # page image is — linked from each page anchor so prose can be checked, not just crops.
    if config.page_images:
        pages = (
            set(range(1, len(result.page_sizes) + 1))
            if config.page_images_all_pages else run.ocr_pages
        )
        if pages:
            run.progress.stage("rendering %d page images", len(pages))
        run.page_rasters = _render_pages(pdf_path, pages, run.assets, config)

    # Re-OCR each scanned figure's crop upright. The engine reads a sideways scan's small text
    # (axis ticks, titles) as garbage; a derotated re-read recovers it clean. Model-free;
    # born-digital figures aren't scanned, so they keep their exact text-layer labels.
    if config.ocr_figures:
        _ocr_scanned_figures(result.figures, run.ocr_pages, vdir)
    run.metrics.finish(
        "render",
        crops_requested=crop_count,
        page_images=len(run.page_rasters),
        figure_svg_enabled=config.figure_svg,
        figure_ocr_enabled=config.ocr_figures,
    )


def _equation_evidence(run: _Run) -> None:
    """Optional second readings of the image-backed equations. The crop stays the source."""
    result, config = run.result, run.config
    # Multi-pass: re-transcribe each image-backed equation with a local math-OCR
    # model so its hint beats the engine's garbled/OCR LaTeX. The crop stays the
    # authoritative source, so this only ever improves the rendering beside it.
    if config.transcribe_equations:
        run.transcriber = run.transcriber or get_transcriber(config)
        if run.transcriber is not None:
            transcribe_equations(
                result.blocks,
                run.transcriber,
                run.vdir,
                run.document_dir,
                cache_stats=run.vision_cache,
            )

    # Render-back evidence: draw each image-backed equation's LaTeX and compare
    # ink layout against its crop (opt-in; matplotlib). Verdict tiers only.
    if config.check_equation_render:
        from pdf2md.confidence import check_equation_renders

        run.progress.stage("render-checking equation crops")
        check_equation_renders(result.blocks, version_dir=run.vdir)
    run.metrics.finish(
        "equations",
        equations=sum(block.type == BlockType.EQUATION for block in result.blocks),
        transcription_enabled=config.transcribe_equations,
        render_check_enabled=config.check_equation_render,
    )


def _figure_evidence(run: _Run) -> None:
    """Chart data, printed labels, and crop descriptions. The crop stays the source."""
    result, config, describer = run.result, run.config, run.describer
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
                run.pdf_path,
                config,
                describer,
                run.vdir,
                progress=run.progress,
                cache_stats=run.vision_cache,
            )
    chart_cache = run.vision_cache.since(run.cache_checkpoint)
    run.metrics.finish(
        "charts",
        enabled=config.digitize_figures or config.digitize_vlm,
        third_party_warning_types=len(chart_warnings.counts),
        third_party_warning_repeats=chart_warnings.repeat_count,
        **{f"vision_cache_{name}": value for name, value in chart_cache.items()},
        **chart_counts,
    )
    run.cache_checkpoint = run.vision_cache.snapshot()

    # Read the printed labels off each figure (axis titles, peak/data labels, legend) —
    # OCR of what's written, reliable where curve digitization can't be. Crop stays source.
    if config.figure_labels and describer is not None:
        _label_figures(
            result.figures,
            describer,
            config,
            run.vdir,
            run.document_dir,
            run.pdf_path,
            cache_stats=run.vision_cache,
        )

    # Lift a figure's caption out of its recovered labels into the caption field, so it renders
    # as the figure's own caption (visible, searchable) instead of buried in the label list.
    _promote_figure_captions(result.figures)

    # Describe each crop (figure, image-fallback table, image-backed equation) with a
    # vision model so the opaque PNG carries a text aid. The crop stays authoritative.
    if config.describe_figures:
        describer = run.describer = describer or get_describer(config)
        if describer is not None:
            _describe_crops(
                result.figures,
                result.blocks,
                describer,
                run.vdir,
                config,
                cache_stats=run.vision_cache,
            )

    # A degraded vision run (endpoint dropped connections under load) must not pass as a
    # clean conversion — the OCR text fell back to the engine and crops have no aid.
    if describer is not None and describer.failures:
        log.warning("%d/%d vision calls failed (endpoint errors) after retries; "
                    "descriptions/OCR are incomplete. Check the endpoint, then rerun the "
                    "same command; completed regions remain cached",
                    describer.failures, describer.calls)
    description_cache = run.vision_cache.since(run.cache_checkpoint)
    run.metrics.finish(
        "descriptions",
        figure_labels_enabled=config.figure_labels,
        crop_descriptions_enabled=config.describe_figures,
        vision_calls=getattr(describer, "calls", 0) if describer is not None else 0,
        vision_failures=getattr(describer, "failures", 0) if describer is not None else 0,
        **{f"vision_cache_{name}": value for name, value in description_cache.items()},
    )


def _emit_bundle(run: _Run) -> None:
    """Assemble the Document and write the Markdown, metadata, and table artifacts."""
    result, config, vdir = run.result, run.config, run.vdir
    run.doc = Document(
        doc_id=run.doc_id,
        source_path=str(run.pdf_path),
        source_sha256=run.doc_id,
        version=run.version,
        page_count=len(result.page_sizes),
        sections=run.structure.root,
        blocks=result.blocks,
        tables=result.tables,
        figures=result.figures,
    )
    run.document_metadata = build_document_metadata(
        run.doc,
        run.meta,
        section_source=run.structure.section_source,
        grobid_references=run.grobid_references,
    )
    write_document_metadata(vdir, run.document_metadata)
    run.meta["document_type"] = run.document_metadata["document"]["kind"]["value"]
    run.meta["metadata_artifact"] = "metadata.json"
    run.section_roles = {
        section["section_id"]: section["semantic_role"]
        for section in run.document_metadata["sections"]
    }
    run.progress.stage("writing Markdown and table artifacts")
    data_dir = vdir / "data"
    if run.doi_metadata is not None:
        data_dir.mkdir(parents=True, exist_ok=True)
        (vdir / DOI_METADATA_NAME).write_text(
            json.dumps(run.doi_metadata, indent=2, ensure_ascii=False) + "\n"
        )
    if run.grobid_tei:
        data_dir.mkdir(parents=True, exist_ok=True)
        for name, xml_bytes in run.grobid_tei.items():
            if xml_bytes:
                (vdir / name).write_bytes(xml_bytes)
    run.md_files, flags = emit_document(
        run.doc,
        run.structure,
        vdir,
        run.meta,
        result.engine_versions,
        page_rasters=run.page_rasters,
        table_ocr_executable=config.table_ocr_executable,
        table_reference_path=config.table_reference_path,
        progress=run.progress,
        formula_enrichment_enabled=config.do_formula_enrichment,
        emission_index=run.emission_index,
    )
    run.doc.coverage = build_report(run.doc_id, result.blocks, flags)
    run.metrics.finish(
        "emit",
        markdown_files=len(run.md_files),
        emitted=run.doc.coverage.emitted,
        cropped=run.doc.coverage.cropped,
        flagged=run.doc.coverage.flagged,
        dropped=run.doc.coverage.dropped,
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
