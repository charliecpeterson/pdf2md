"""Orchestration: PDF → engine → structure → render ∥ emit → coverage → disk.

`convert_file` is idempotent (content-hash identity, versioned output, no-op
unless `force`). `convert_dir` isolates failures per document so one bad PDF
never aborts a batch.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pdf2md import __version__
from pdf2md.bookmarks import read_bookmarks
from pdf2md.cache import content_hash, doc_dir, latest_version, next_version
from pdf2md.confidence import RECOVER_BELOW
from pdf2md.config import Config
from pdf2md.describe import Describer, get_describer
from pdf2md.enrich import GlyphIndex, enrich_blocks, enrich_figures, enrich_tables
from pdf2md.coverage import build_report
from pdf2md.emit import emit_document
from pdf2md.engines.base import Engine
from pdf2md.logging import get_logger
from pdf2md.metadata import extract_metadata
from pdf2md.profile import build_profile, write_profile, write_readme
from pdf2md.render import CropRenderer
from pdf2md.schema import FORMAT_VERSION, BlockType, CoverageReport, Document, Provenance
from pdf2md.transcribe import Transcriber, get_transcriber
from pdf2md.structure import build_structure

log = get_logger("pipeline")


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


def _get_engine(engine: Engine | None, config: Config) -> Engine:
    if engine is not None:
        return engine
    from pdf2md.engines.docling import DoclingEngine

    return DoclingEngine(
        formula_enrichment=config.do_formula_enrichment,
        artifacts_path=config.local_model_dir,
    )


def convert_file(
    pdf_path: Path,
    *,
    engine: Engine | None = None,
    transcriber: Transcriber | None = None,
    describer: Describer | None = None,
    config: Config | None = None,
    force: bool = False,
) -> ConvertResult:
    pdf_path = Path(pdf_path)
    config = config or Config()
    doc_id = content_hash(pdf_path)
    dd = doc_dir(doc_id)

    cached = latest_version(dd)
    if cached is not None and not force:
        extra = (" (--describe/--transcribe apply on a fresh run; add --force)"
                 if (config.describe_figures or config.transcribe_equations) else "")
        vdir = dd / f"v{cached}"
        log.info("cached: %s (v%d); use force=True to re-convert%s", pdf_path.name, cached, extra)
        prov = vdir / "provenance.json"
        pages = json.loads(prov.read_text()).get("page_count", 0) if prov.exists() else 0
        return ConvertResult(
            doc_id, cached, vdir, sorted(vdir.glob("*.md")), page_count=pages, cached=True
        )

    # Build the optional vision client up front (cheap, no network) so --describe /
    # --ocr-vlm without the extra fails here, before the engine runs and writes a
    # partial dir.
    if (config.describe_figures or config.ocr_vlm) and describer is None:
        describer = get_describer(config)

    started = datetime.now(timezone.utc)
    engine = _get_engine(engine, config)
    try:
        result = engine.convert(pdf_path)
    except Exception as exc:  # noqa: BLE001 - document-level isolate-and-flag
        log.error("engine failed on %s: %s", pdf_path.name, exc)
        return ConvertResult(doc_id, 0, dd, [], failed=True, error=str(exc))

    # Engine-agnostic verification layer (scripts, ligatures, equation cross-check,
    # OCR detection), off the engine so any backend inherits it.
    if config.detect_scripts:
        with GlyphIndex(pdf_path) as glyphs:
            enrich_blocks(result.blocks, glyphs)
            enrich_tables(result.tables, result.raw_tables, glyphs)
            enrich_figures(result.figures, glyphs)

    # Re-OCR scanned prose with the vision model (better than the engine's RapidOCR),
    # before metadata/structure so they read the cleaned text. The page image stays
    # the source of truth.
    if config.ocr_vlm and describer is not None:
        _vlm_ocr_scanned(result.blocks, describer, pdf_path, config, dd)

    bookmarks = read_bookmarks(pdf_path)
    meta = extract_metadata(pdf_path, result.blocks)
    structure = build_structure(
        result.blocks,
        bookmarks,
        title=meta.get("title") or pdf_path.stem,
        page_count=len(result.page_sizes),
    )

    version = next_version(dd)
    vdir = dd / f"v{version}"
    assets = vdir / "assets"

    crop_blocks = _eq_crops(result.blocks) + _table_crops(result.blocks, result.tables)
    _render_crops(pdf_path, result.figures, crop_blocks, assets, config)

    # Multi-pass: re-transcribe each image-backed equation with a local math-OCR
    # model so its hint beats the engine's garbled/OCR LaTeX. The crop stays the
    # authoritative source, so this only ever improves the rendering beside it.
    if config.transcribe_equations:
        transcriber = transcriber or get_transcriber(config)
        if transcriber is not None:
            _transcribe_equations(result.blocks, transcriber, vdir)

    # Describe each crop (figure, image-fallback table, image-backed equation) with a
    # vision model so the opaque PNG carries a text aid. The crop stays authoritative.
    if config.describe_figures:
        describer = describer or get_describer(config)
        if describer is not None:
            _describe_crops(result.figures, result.blocks, describer, vdir)

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
    md_files, flags = emit_document(doc, structure, vdir, meta, result.engine_versions)
    doc.coverage = build_report(doc_id, result.blocks, flags)

    # Per-doc profile, surfaced for an AI (profile.json) and a human (README.md).
    profile = build_profile(doc)
    write_profile(vdir, doc, profile, md_files)
    write_readme(vdir, doc, meta, profile, md_files)

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
        duration_s=round((finished - started).total_seconds(), 2),
        section_source=structure.section_source,
    )
    (vdir / "provenance.json").write_text(json.dumps(doc.to_dict(), indent=2, default=str))

    log.info(
        "converted %s -> v%d (%d md files, %s)",
        pdf_path.name, version, len(md_files),
        "lossless" if doc.coverage.lossless else "INCOMPLETE",
    )
    return ConvertResult(
        doc_id, version, vdir, md_files, coverage=doc.coverage, page_count=doc.page_count
    )


def convert_dir(
    root: Path,
    *,
    engine: Engine | None = None,
    config: Config | None = None,
    force: bool = False,
) -> list[ConvertResult]:
    root = Path(root)
    pdfs = sorted(root.rglob("*.pdf"))
    if not pdfs:
        log.warning("no PDFs under %s", root)
        return []
    config = config or Config()
    engine = _get_engine(engine, config)  # build once, reuse across the batch
    transcriber = get_transcriber(config)  # loads the math-OCR model once, if enabled
    describer = get_describer(config)      # one vision client, reused across the batch
    results: list[ConvertResult] = []
    for pdf in pdfs:
        try:
            results.append(convert_file(
                pdf, engine=engine, transcriber=transcriber, describer=describer,
                config=config, force=force))
        except Exception as exc:  # noqa: BLE001 - poison-pill isolation
            log.error("unhandled failure on %s: %s", pdf.name, exc)
            results.append(
                ConvertResult(content_hash(pdf), 0, root, [], failed=True, error=str(exc))
            )
    return results


def _eq_crops(blocks) -> list:
    """Low-confidence equations whose extraction is suspect; their image crop is the
    faithful source (the LaTeX/text reading may be garbled or scrambled)."""
    return [
        b for b in blocks
        if b.type is BlockType.EQUATION and b.bbox is not None
        and b.confidence is not None and b.confidence < RECOVER_BELOW
    ]


def _transcribe_equations(blocks, transcriber, vdir: Path) -> None:
    """Store a better hint on each image-backed equation from re-OCR'ing its crop."""
    for b in blocks:
        crop = b.extra.get("crop_path")
        if b.type is BlockType.EQUATION and crop:
            latex = transcriber.transcribe(vdir / crop)
            if latex:
                b.extra["transcribed"] = latex
                b.extra["transcribed_source"] = "math OCR"


# Scanned-page block types whose OCR text the vision model re-transcribes (equations
# and tables are image-backed; figures are crops).
_PROSE_OCR = {"paragraph", "heading", "list", "caption", "footnote", "other"}


def _vlm_ocr_scanned(blocks, describer: Describer, pdf_path: Path, config: Config,
                     doc_dir: Path) -> None:
    """Replace each scanned prose block's RapidOCR text with a vision-model
    transcription of the block's crop. Cropped to a temp dir (the text replaces it, so
    no asset is kept); cached at the doc level by (model, crop bytes), so a re-run
    doesn't re-OCR. Best-effort — a block the model can't read keeps its engine text."""
    targets = [b for b in blocks if b.extra.get("ocr") and b.bbox is not None
               and b.type.value in _PROSE_OCR and b.text.strip()]
    if not targets:
        return
    doc_dir.mkdir(parents=True, exist_ok=True)
    cache_file = doc_dir / "describe_cache.json"
    cache: dict = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    model = describer.model_for("ocr")

    with tempfile.TemporaryDirectory() as td, \
            CropRenderer(pdf_path, dpi=config.crop_dpi, padding_pts=config.crop_padding_pts) as cr:
        tmp = Path(td)
        for b in targets:
            crop = tmp / f"{b.id.strip('#/').replace('/', '_')}.png"
            try:
                cr.crop(b.page, b.bbox, crop)
            except Exception as exc:  # noqa: BLE001 - page-level isolate-and-flag
                log.warning("ocr crop failed for %s: %s", b.id, exc)
                continue
            key = f"{model}:ocr:{hashlib.sha256(crop.read_bytes()).hexdigest()}"
            text = cache.get(key)
            if text is None:
                text = describer.describe(crop, "ocr", "")
                if text:
                    cache[key] = text
            if text:
                b.text = text
                b.extra["text_source"] = "vlm-ocr"
    cache_file.write_text(json.dumps(cache, indent=2))


def _describe_crops(figures, blocks, describer: Describer, vdir: Path) -> None:
    """Add a vision-model description to every rendered crop: figures and image-backed
    tables get a labelled aid beside the image; an equation's transcription rides as
    its hint (unless math-OCR already filled it). Best-effort — a crop with no
    description just keeps what it had.

    Descriptions are cached at the doc level by (model, kind, crop bytes), so a
    `--force` re-run or a re-render with the same crops doesn't pay the vision model
    again. The model is the one routed for that kind, so swapping the figure or OCR
    model misses correctly; the crop's pixels are the key, so a DPI change misses too."""
    cache_file = vdir.parent / "describe_cache.json"
    cache: dict = json.loads(cache_file.read_text()) if cache_file.exists() else {}

    def described(crop_rel: str, kind: str, context: str) -> str | None:
        path = vdir / crop_rel
        key = f"{describer.model_for(kind)}:{kind}:{hashlib.sha256(path.read_bytes()).hexdigest()}"
        if key in cache:
            return cache[key]
        text = describer.describe(path, kind, context)
        if text:
            cache[key] = text
        return text

    for fig in figures:
        if fig.asset_path:
            desc = described(fig.asset_path, "figure", fig.caption or "")
            if desc:
                fig.description = desc
    for b in blocks:
        crop = b.extra.get("crop_path")
        if not crop:
            continue
        if b.type is BlockType.EQUATION:
            if not b.extra.get("transcribed"):
                latex = described(crop, "equation", b.text or "")
                if latex:
                    b.extra["transcribed"] = latex
                    b.extra["transcribed_source"] = "vision model"
        else:  # image-fallback table
            desc = described(crop, "table", b.text or "")
            if desc:
                b.extra["description"] = desc

    cache_file.write_text(json.dumps(cache, indent=2))


def _table_crops(blocks, tables) -> list:
    """Tables to image-back: ones Docling failed to parse into cells (kept a bbox
    but no renderable content, would otherwise drop), and ones on an OCR'd scan
    page (the cells are OCR guesses, so the scan pixels are the ground truth)."""
    rendered = {t.block_id for t in tables if (t.gfm or "").strip() or t.html}
    return [
        b for b in blocks
        if b.bbox is not None and b.id.startswith("#/tables/")
        and (b.id not in rendered or b.extra.get("ocr"))
    ]


def _render_crops(pdf_path: Path, figures, eq_blocks, assets: Path, config: Config) -> None:
    if not figures and not eq_blocks:
        return
    with CropRenderer(pdf_path, dpi=config.crop_dpi, padding_pts=config.crop_padding_pts) as cr:
        for fig in figures:
            if fig.bbox is None:
                continue
            name = f"{fig.block_id.strip('#/').replace('/', '_')}_p{fig.page}.png"
            try:
                cr.crop(fig.page, fig.bbox, assets / name)
                fig.asset_path = f"assets/{name}"
            except Exception as exc:  # noqa: BLE001 - page-level isolate-and-flag
                log.warning("crop failed for %s: %s", fig.block_id, exc)
        for b in eq_blocks:
            name = f"{b.id.strip('#/').replace('/', '_')}_p{b.page}.png"
            try:
                cr.crop(b.page, b.bbox, assets / name)
                b.extra["crop_path"] = f"assets/{name}"
            except Exception as exc:  # noqa: BLE001 - page-level isolate-and-flag
                log.warning("equation crop failed for %s: %s", b.id, exc)
