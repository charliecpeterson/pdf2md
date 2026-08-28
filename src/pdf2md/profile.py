"""Build the document inventory and evidence-backed quality scorecard.

The same profile feeds profile.json, the generated README, and the accuracy harness,
so human and machine readers see the same evidence and limitations.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from pdf2md.confidence import plot_data_accepted
from pdf2md.document_metadata import METADATA_NAME
from pdf2md.enrich import recall_summary
from pdf2md.grobid import REFS_TEI_NAME
from pdf2md.logging import _duration
from pdf2md.quality import build_quality_scorecard
from pdf2md.review import build_review_queue
from pdf2md.run_metrics import failed_optional_calls
from pdf2md.schema import (
    FORMAT_VERSION,
    PROSE_TYPES,
    BlockType,
    CoverageStatus,
    Document,
    DocumentProfile,
)
from pdf2md.tables import table_has_content

_GRADES = ("high", "medium", "low")  # ordered best -> worst


def _downgrade(current: str, to: str) -> str:
    return to if _GRADES.index(to) > _GRADES.index(current) else current


def _sufficiency(doc: Document) -> tuple[int, dict[str, int]]:
    """Split the document's elements into text-sufficient (usable from the markdown alone) and
    pixel-authoritative (the crop is the real record). A figure is text-sufficient only when its
    data was recovered (reconstructable); a table when it has structured cells and isn't an OCR
    scan (whose cells are guesses); an equation when its LaTeX was verified (not image-backed);
    prose whenever it's legible. This is the honest measure of how close the doc is to needing no
    `assets/`, orthogonal to accounting (a scanned figure can be represented by its crop but not text-
    sufficient). Returns (text-sufficient count, pixel-authoritative breakdown by kind)."""
    ocr_blocks = {b.id for b in doc.blocks
                  if b.extra.get("ocr") or b.extra.get("cells_unverified")}
    pixel: Counter[str] = Counter()
    text = 0
    for f in doc.figures:  # a figure is reconstructable only with recovered data
        if plot_data_accepted(f.digitization):
            text += 1
        else:
            pixel["image-only figures"] += 1  # a scan, embedded raster, structure, or scheme
    table_blocks = {b.id for b in doc.blocks if b.type is BlockType.TABLE}
    for t in doc.tables:  # structured cells, and not an OCR scan whose cells are guesses
        if t.block_id not in table_blocks:
            continue  # subsumed into a page transcription (--ocr-page-vlm) — counted as its text
        if ((t.gfm or "").strip() or t.html or t.preformatted) and t.block_id not in ocr_blocks:
            text += 1
        else:
            pixel["image-backed tables"] += 1
    for b in doc.blocks:
        if b.type in (BlockType.FIGURE, BlockType.TABLE):
            continue  # counted from doc.figures / doc.tables above
        if b.type is BlockType.EQUATION:  # checked before the DROPPED skip: a --no-formula
            if b.extra.get("crop_path"):  # equation is DROPPED but still has an authoritative crop
                pixel["image-backed equations"] += 1        # LaTeX unverified, crop is the source
            elif b.coverage_status in (CoverageStatus.FLAGGED, CoverageStatus.DROPPED):
                pixel["untranscribed equations"] += 1       # --no-formula: a marker, no LaTeX or crop
            else:
                text += 1                                   # verified LaTeX
        elif b.coverage_status is CoverageStatus.DROPPED:
            continue  # an empty-block marker (prose): no content to classify
        elif b.coverage_status is CoverageStatus.FLAGGED:
            pixel["illegible prose"] += 1                   # the page raster is the record
        else:
            text += 1
    return text, dict(pixel)


def build_profile(
    doc: Document,
    consistency: dict | None = None,
    metadata: dict | None = None,
    engine_quality: dict | None = None,
    review_queue: dict | None = None,
) -> DocumentProfile:
    """`consistency` is the numeric-conservation report from enrich.numeric_conservation;
    None (tests, older callers) records the signal as not computed. Word recall is
    aggregated from the per-block measurements enrichment already recorded."""
    blocks = doc.blocks
    by_type = Counter(b.type.value for b in blocks)
    eqs = [b for b in blocks if b.type is BlockType.EQUATION]
    image_backed = sum(1 for b in eqs if b.extra.get("crop_path"))
    ocr_pages = len({b.page for b in blocks if b.extra.get("ocr")})
    blocks_by_id = {b.id: b for b in blocks}
    def _unverified(block_id: str) -> bool:
        block = blocks_by_id.get(block_id)
        return bool(block and (block.extra.get("ocr")
                               or block.extra.get("cells_unverified")))

    table_candidates = sum(
        1 for table in doc.tables
        if table_has_content(table)
        and _unverified(table.block_id)
    )
    tables_verified = sum(
        1 for table in doc.tables
        if table_has_content(table)
        and not _unverified(table.block_id)
    )
    tables_image_only = len(doc.tables) - table_candidates - tables_verified
    derived_table_datasets = len({
        table.normalized_json_path for table in doc.tables if table.normalized_json_path
    })
    table_cell_evidence: Counter[str] = Counter()
    table_cell_resolution: Counter[str] = Counter()
    glyph_check: dict[str, int] = {}
    for table in doc.tables:
        table_cell_evidence.update(table.cell_evidence_counts)
        table_cell_resolution.update(table.cell_resolution_counts)
        for key, value in table.cell_glyph_check.get("cells", {}).items():
            glyph_check[key] = glyph_check.get(key, 0) + value
        uncovered = table.cell_glyph_check.get("uncovered_glyphs", 0)
        if uncovered:
            glyph_check["uncovered_glyphs"] = glyph_check.get("uncovered_glyphs", 0) + uncovered

    prose = [b for b in blocks if b.type in PROSE_TYPES and b.text.strip()]
    render_support: Counter[str] = Counter(
        b.extra["render_support"]
        for b in eqs
        if b.extra.get("render_support") in ("supported", "unsupported")
    )
    render_check: Counter[str] = Counter(
        b.extra["render_check"]["verdict"]
        for b in eqs
        if isinstance(b.extra.get("render_check"), dict)
        and b.extra["render_check"].get("verdict")
    )
    illegible = doc.coverage.illegible if doc.coverage else 0
    legibility = (len(prose) - illegible) / len(prose) if prose else 1.0
    accounted_for = doc.coverage.accounted_for if doc.coverage else False
    complete = doc.coverage.complete if doc.coverage else False
    review_reasons = Counter(f.reason for f in doc.coverage.flags) if doc.coverage else Counter()
    review_flags = sum(review_reasons.values())
    review_counts = (review_queue or {}).get("counts", {})
    needs_review = (
        bool(review_counts.get("action_required"))
        if review_queue is not None else doc.coverage.needs_review if doc.coverage else True
    )

    ocr_by_vlm = any(b.extra.get("text_source") in ("vlm-ocr", "vlm-page") for b in blocks)
    vlm_pages = len({b.page for b in blocks if b.extra.get("text_source") == "vlm-page"})
    grade, reasons = _confidence(
        accounted_for, illegible, ocr_pages, doc.page_count,
        len(eqs), image_backed,
        flagged=doc.coverage.flagged if doc.coverage else 0,
        dropped=doc.coverage.dropped if doc.coverage else 0,
        review_reasons=review_reasons,
        ocr_by_vlm=ocr_by_vlm,
        vlm_pages=vlm_pages,
    )
    text_sufficient, pixel_by = _sufficiency(doc)
    equations_text = sum(
        1 for block in eqs
        if block.text.strip()
        and not block.extra.get("crop_path")
        and block.coverage_status not in (CoverageStatus.FLAGGED, CoverageStatus.DROPPED)
    )
    scorecard = build_quality_scorecard(
        doc,
        text_sufficient=text_sufficient,
        pixel_authoritative=sum(pixel_by.values()),
        equations_text=equations_text,
        tables_verified=tables_verified,
        metadata=metadata,
        engine_quality=engine_quality,
        review_queue=review_queue,
    )
    incomplete_content = any(
        scorecard["dimensions"][name]["status"] in {"partial", "none"}
        for name in (
            "text_sufficiency",
            "equation_text_coverage",
            "table_verification_coverage",
            "figure_text_data_coverage",
        )
    )
    if grade == "high" and incomplete_content:
        grade = "medium"
        reasons.append("some content remains image-dependent or unverified; see quality_scorecard")
    recall = recall_summary(blocks)
    conservation = consistency or {"available": False, "reason": "not computed"}
    return DocumentProfile(
        pages=doc.page_count,
        blocks=len(blocks),
        by_type=dict(by_type),
        figures=len(doc.figures),
        tables=len(doc.tables),
        tables_verified=tables_verified,
        tables_candidates=table_candidates,
        tables_image_only=tables_image_only,
        derived_table_datasets=derived_table_datasets,
        table_cell_evidence=dict(table_cell_evidence),
        table_cell_resolution=dict(table_cell_resolution),
        table_cell_glyph_check=glyph_check,
        equation_render_check=dict(render_check),
        equation_render_support=dict(render_support),
        equations=len(eqs),
        equations_image_backed=image_backed,
        code_blocks=by_type.get("code", 0),
        illegible_blocks=illegible,
        ocr_pages=ocr_pages,
        vlm_pages=vlm_pages,
        accounted_for=accounted_for,
        complete=complete,
        needs_review=needs_review,
        review_flags=review_flags,
        review_reasons=dict(review_reasons),
        encoding_legibility=round(legibility, 4),
        text_sufficient=text_sufficient,
        pixel_authoritative=sum(pixel_by.values()),
        pixel_authoritative_by=pixel_by,
        confidence=grade,
        confidence_reasons=reasons,
        glyph_recall_blocks=recall["blocks_measured"],
        glyph_recall_words_total=recall["words_total"],
        glyph_recall_words_matched=recall["words_matched"],
        glyph_low_recall_blocks=recall["low_recall_blocks"],
        numeric_conservation=conservation,
        quality_scorecard=scorecard,
        review_counts=review_counts,
    )


def _confidence(accounted_for, illegible, ocr_pages, pages, equations, image_backed,
                *, flagged=0, dropped=0, review_reasons=None, ocr_by_vlm=False, vlm_pages=0):
    grade = "high"
    reasons: list[str] = []
    # Be honest about partial vision-model coverage: a page whose VLM transcription came back
    # empty keeps its engine OCR, so claiming the whole doc is "OCR by a vision model" overstates it.
    if vlm_pages and vlm_pages < ocr_pages:
        by = f"OCR by a vision model on {vlm_pages}/{ocr_pages} pages, engine OCR on the rest — verify"
    elif ocr_by_vlm:
        by = "OCR by a vision model"
    else:
        by = "OCR text"
    if not accounted_for:
        grade = _downgrade(grade, "low")
        reasons.append("some detected blocks have no recorded disposition")
    if dropped:
        grade = _downgrade(grade, "low")
        reasons.append(f"{dropped} detected block(s) have no usable representation")
    if flagged:
        grade = _downgrade(grade, "low" if flagged > 5 else "medium")
        reasons.append(f"{flagged} detected block(s) require review")
    if illegible:
        grade = _downgrade(grade, "low" if illegible > 5 else "medium")
        reasons.append(f"{illegible} illegible block(s) — broken font not recovered")
    if pages and ocr_pages / pages > 0.5:
        grade = _downgrade(grade, "medium")
        reasons.append(f"{ocr_pages}/{pages} pages scanned — {by}, verify against the images")
    elif ocr_pages:
        reasons.append(f"{ocr_pages} scanned page(s) — {by}, not a born-digital layer")
    if equations and image_backed:
        reasons.append(f"{image_backed}/{equations} equations image-backed — LaTeX unverified, "
                       "the crop is authoritative")
    for reason, count in (review_reasons or {}).items():
        reasons.append(f"{count} review marker(s): {reason}")
    if not reasons:
        reasons.append("clean born-digital extraction, nothing flagged")
    return grade, reasons


def write_profile(version_dir: Path, doc: Document, profile: DocumentProfile,
                  md_files: list[Path]) -> Path:
    """profile.json: the profile plus the output file list and a pointer to the
    contents tree — the machine-readable 'what is this and how do I read it'."""
    names = [p.name for p in md_files]
    data = {
        "doc_id": doc.doc_id[:16],
        "source_sha256": doc.source_sha256,
        "source": Path(doc.source_path).name,
        **asdict(profile),
        "confidence_deprecated": True,
        "files": names,
        "contents": "index.md" if "index.md" in names else (names[0] if names else None),
    }
    path = version_dir / "profile.json"
    path.write_text(json.dumps(data, indent=2))
    return path


def write_manifest(
    version_dir: Path,
    doc: Document,
    meta: dict,
    profile: DocumentProfile,
    md_files: list[Path],
    page_rasters: dict[int, str],
    review_queue: dict | None = None,
    passage_count: int = 0,
    document_metadata: dict | None = None,
) -> Path:
    """Write the compact navigation map; full content and lineage stay elsewhere."""
    markdown = [path.name for path in md_files]
    start = "index.md" if "index.md" in markdown else (markdown[0] if markdown else None)
    blocks = {block.id: block for block in doc.blocks}
    review_queue = review_queue or build_review_queue(doc)
    selected_metadata = {
        field: meta.get(field)
        for field in ("title", "authors", "year", "doi", "venue")
    }
    selected_metadata.update({
        field: meta[field]
        for field in (
            "publisher", "volume", "issue", "pages", "article_number",
            "citation_locator", "issn", "isbn", "edition", "publication_dates",
        )
        if meta.get(field) is not None
    })
    metadata_record = {
        "selected": selected_metadata,
        "evidence": meta.get("metadata_evidence"),
    }
    if document_metadata is not None:
        metadata_record.update({
            "path": METADATA_NAME,
            "document_type": meta.get("document_type"),
        })

    equations = []
    for block in doc.blocks:
        if block.type is not BlockType.EQUATION:
            continue
        crop = block.extra.get("crop_path")
        equations.append({
            "block_id": block.id,
            "page": block.page,
            "representation": "image_with_text_hint" if crop else "latex",
            "crop": crop,
        })

    table_blocks = {table.block_id: table for table in doc.tables}
    tables = []
    for block_id, table in table_blocks.items():
        block = blocks.get(block_id)
        has_content = table_has_content(table)
        is_ocr = bool(block and (block.extra.get("ocr")
                                 or block.extra.get("cells_unverified")))
        crop = block.extra.get("crop_path") if block else None
        if is_ocr and has_content:
            representation = (
                "image_with_ocr_candidate" if crop else "ocr_candidate_without_crop"
            )
        elif crop and not has_content:
            representation = "image_only"
        elif table.preformatted:
            representation = "preformatted"
        elif table.html:
            representation = "html_and_markdown"
        else:
            representation = "markdown"
        tables.append({
            "block_id": block_id,
            "page": table.page,
            "representation": representation,
            "authority": "image" if is_ocr else "structured",
            "crop": crop,
            "candidate": table.candidate_path or None,
            "csv": table.data_path or None,
            "json": table.json_path or None,
            "normalized_csv": table.normalized_data_path or None,
            "normalized_json": table.normalized_json_path or None,
            "cell_evidence": table.cell_evidence_path or None,
            "cell_evidence_counts": table.cell_evidence_counts,
            "cell_resolution_counts": table.cell_resolution_counts,
        })

    data = {
        "schema_version": 1,
        "format_version": FORMAT_VERSION,
        "document": {
            "id": doc.doc_id,
            "version": doc.version,
            "title": meta.get("title") or Path(doc.source_path).stem,
            "pages": doc.page_count,
        },
        "metadata": metadata_record,
        "source": {
            "path": "../source.pdf",
            "sha256": doc.source_sha256,
        },
        "read": {
            "start": start,
            "markdown": markdown,
            "chunks": "chunks.jsonl",
            "passages": "passages.jsonl",
            "passage_schema": "passages.schema.json",
            "outline": "outline.json",
            "symbols": "symbols.json",
            "profile": "profile.json",
            "provenance": "provenance.json",
            "base_state": "base-state.json",
            "review": "review.md",
            "review_queue": "review.json",
            **({"metadata": METADATA_NAME} if document_metadata is not None else {}),
        },
        "inventory": {
            "blocks": profile.blocks,
            "by_type": profile.by_type,
            "figures": profile.figures,
            "tables": profile.tables,
            "derived_table_datasets": profile.derived_table_datasets,
            "equations": profile.equations,
            "passages": passage_count,
        },
        "quality": {
            "confidence": profile.confidence,
            "confidence_deprecated": True,
            "scorecard": profile.quality_scorecard,
            "accounted_for": profile.accounted_for,
            "complete": profile.complete,
            "needs_review": profile.needs_review,
            "review_flags": profile.review_flags,
            "review_counts": review_queue["counts"],
            "table_cell_evidence": profile.table_cell_evidence,
            "table_cell_resolution": profile.table_cell_resolution,
            "text_sufficient": profile.text_sufficient,
            "pixel_authoritative": profile.pixel_authoritative,
        },
        "representations": {
            "figures": [{
                "block_id": figure.block_id,
                "page": figure.page,
                "image": figure.asset_path or None,
                "svg": figure.svg_path or None,
                "data": figure.data_path or None,
                "code": figure.code_path or None,
                "has_structured_data": bool(figure.data_path),
                "data_extraction_status": figure.data_extraction_status,
                "data_extraction_note": figure.data_extraction_note or None,
            } for figure in doc.figures],
            "tables": tables,
            "equations": equations,
            "page_images": [
                {"page": page, "path": path}
                for page, path in sorted(page_rasters.items())
            ],
        },
        "review": review_queue["items"],
    }
    if document_metadata is not None:
        references = document_metadata.get("references") or {}
        data["references"] = {
            "path": METADATA_NAME,
            "count": references.get("count", 0),
            "source": "local_and_grobid" if references.get("structured_source") else "local",
            "structured_source": references.get("structured_source"),
        }
    elif meta.get("references_count") is not None:
        data["references"] = {
            "path": REFS_TEI_NAME,
            "count": meta.get("references_count"),
            "source": "grobid",
        }
    path = version_dir / "manifest.json"
    path.write_text(json.dumps(data, indent=2))
    return path


def _run_summary_lines(run_metrics: dict | None) -> list[str]:
    if not run_metrics:
        return []
    stages = run_metrics.get("stages", {})
    timings = ", ".join(
        f"{name} {_duration(float(stage['duration_s']))}"
        for name, stage in stages.items()
    )
    lines = [
        "## Conversion work",
        "",
        f"Recorded wall time: {_duration(float(run_metrics['duration_s']))}.",
        f"Stages: {timings}.",
    ]
    chart = stages.get("charts", {}).get("counts", {})
    if chart.get("enabled"):
        lines.append(
            f"Charts: {chart['attempted']} attempted, {chart['accepted']} accepted, "
            f"{chart['declined']} declined, {chart['failed']} failed; "
            f"{chart['ocr_axis_attempted']} OCR-axis attempts and "
            f"{chart['ocr_axis_ineligible']} geometrically ineligible."
        )
    cache = {
        name: sum(
            int(stage.get("counts", {}).get(f"vision_cache_{name}", 0))
            for stage in stages.values()
        )
        for name in ("lookups", "hits", "misses", "writes")
    }
    if cache["lookups"]:
        lines.append(
            f"Vision cache: {cache['hits']} of {cache['lookups']} lookups served from cache, "
            f"{cache['misses']} misses, and {cache['writes']} new result(s) stored."
        )
    optional_failures = failed_optional_calls(run_metrics)
    if optional_failures:
        lines.append(
            f"Optional model work is partial: {optional_failures} call(s) failed after "
            "retries. The bundle remains usable; rerunning the same command retries "
            "missing regions and reuses completed ones."
        )
    memory = run_metrics.get("memory", {})
    if memory.get("available"):
        main_mib = memory["main_process_peak_rss_bytes"] / 1024**2
        lines.append(
            f"Main-process peak RSS: {main_mib:,.1f} MiB "
            "(process-lifetime high-water mark)."
        )
        if child := memory.get("largest_terminated_child_peak_rss_bytes"):
            lines.append(
                f"Largest terminated child peak RSS: {child / 1024**2:,.1f} MiB."
            )
    lines += ["Exact stage timings and work counts are in `provenance.json`.", ""]
    return lines


def _scorecard_lines(profile: DocumentProfile) -> list[str]:
    dimensions = profile.quality_scorecard.get("dimensions", {})

    def _result(name: str) -> str:
        dimension = dimensions[name]
        ratio = dimension.get("ratio")
        counts = (
            f" ({dimension['numerator']}/{dimension['denominator']})"
            if ratio is not None else ""
        )
        return f"{dimension['status']}{counts}"

    rows = [
        ("Accounting coverage", _result("accounting_coverage")),
        ("Structural completeness", _result("structural_completeness")),
        ("Text sufficiency", _result("text_sufficiency")),
        ("Layout quality", _result("layout_quality")),
        ("OCR dependence", _result("ocr_dependence")),
        ("OCR quality", _result("ocr_quality")),
        ("Equation text coverage", _result("equation_text_coverage")),
        ("Table verification coverage", _result("table_verification_coverage")),
        ("Figure text/data coverage", _result("figure_text_data_coverage")),
        ("Metadata quality", _result("metadata_quality")),
        ("Unresolved error severity", _result("unresolved_error_severity")),
    ]
    return [
        "## Quality scorecard",
        "",
        "Each result is an independent evidence summary, not a probability.",
        "",
        "| Dimension | Result |",
        "|---|---|",
        *[f"| {label} | {result} |" for label, result in rows],
        "",
        f"Legacy aggregate label: {profile.confidence} (deprecated and uncalibrated).",
        "Evidence sources, calibration status, counts, and notes are in `profile.json`.",
        "",
    ]


def _metadata_evidence_lines(meta: dict) -> list[str]:
    evidence = meta.get("metadata_evidence")
    if not evidence:
        return []
    lines = ["## Bibliographic metadata", ""]
    for label, field in (("Title", "title"), ("Authors", "authors")):
        field_evidence = evidence[field]
        selected = field_evidence.get("selected")
        if selected is None:
            lines.append(f"{label}: no local candidate selected.")
            continue
        value = selected["value"]
        rendered = ", ".join(value) if isinstance(value, list) else value
        sources = ", ".join(dict.fromkeys(
            item["source"] for item in selected["evidence"]
        ))
        lines.append(
            f"{label}: {rendered}. Evidence quality: {selected['quality']}; "
            f"source(s): {sources}."
        )
    lines.extend([
        "Ranked alternatives, penalties, rejection reasons, and exact page/block "
        "evidence are in `manifest.json`.",
        "",
    ])
    return lines


def _conservation_lines(report: dict) -> list[str]:
    lines = []
    if report.get("available"):
        lines.append(
            f"Numeric conservation: {report['conserved_values']} of {report['source_values']} "
            "numeric value(s) in the embedded text layer appear in the output"
            + (
                f"; {report['missing_values']} missing (examples in `profile.json`)."
                if report.get("missing_values") else "."
            )
        )
    representation = report.get("representation_aware")
    if representation:
        categories = representation["categories"]
        loss = categories["unexplained_loss"]
        addition = categories["unexplained_addition"]
        dependent = categories["expected_source_dependent"]
        lines.append(
            "Representation-aware conservation: "
            f"{loss['words']} unexplained word loss(es), {loss['numbers']} unexplained "
            f"number loss(es), {addition['words']} unexplained word addition(s), and "
            f"{addition['numbers']} unexplained number addition(s). "
            f"Source-authoritative regions account for {dependent['words']} word(s) and "
            f"{dependent['numbers']} number(s); exact records are in `profile.json`."
        )
    return lines


def write_readme(version_dir: Path, doc: Document, meta: dict, profile: DocumentProfile,
                 md_files: list[Path], *, run_metrics: dict | None = None,
                 passage_count: int | None = None) -> Path:
    """README.md: a human run summary — what the doc is, what's in it, how much to
    trust it, and where to start. Renders first when the output folder is opened."""
    names = [p.name for p in md_files]
    contents = "index.md" if "index.md" in names else (names[0] if names else "the markdown files")
    p = profile
    review_counts = p.review_counts or {
        "action_required": p.review_flags,
        "source_dependent": 0,
        "informational": 0,
    }
    inv = ", ".join(f"{n} {label}" for n, label in [
        (p.equations, f"equations ({p.equations_image_backed} image-backed)" if p.equations else ""),
        (p.tables, "source tables"),
        (p.derived_table_datasets, "derived table datasets"),
        (p.figures, "figures"), (p.code_blocks, "code blocks"),
    ] if n) or "text only"

    lines = [
        f"# {meta.get('title') or Path(doc.source_path).stem} — conversion summary",
        "",
        f"`{Path(doc.source_path).name}` · {p.pages} pages · `doc_id {doc.doc_id[:16]}` · "
        f"converted by pdf2md.",
        "",
        *_scorecard_lines(p),
        *_metadata_evidence_lines(meta),
        "## Coverage",
        "",
        f"Detected blocks accounted for: {'yes' if p.accounted_for else 'no'}.",
        f"Structural representation complete: {'yes' if p.complete else 'no'}.",
        f"Action required: {'yes' if review_counts['action_required'] else 'no'} "
        f"({review_counts['action_required']} item(s)).",
        f"Source-dependent entries: {review_counts['source_dependent']}.",
        f"Informational entries: {review_counts['informational']}.",
        (
            f"Tables: {p.tables_verified} verified structured, "
            f"{p.tables_candidates} structured OCR candidate(s), "
            f"{p.tables_image_only} image-only."
        ),
        f"Derived normalized table datasets: {p.derived_table_datasets}.",
        *(
            [f"Embedded-text-layer word recall: {p.glyph_recall_words_matched} of "
             f"{p.glyph_recall_words_total} word(s) across {p.glyph_recall_blocks} block(s)"
             + (f"; {p.glyph_low_recall_blocks} below 90% — check `profile.json`."
                if p.glyph_low_recall_blocks else ".")]
            if p.glyph_recall_blocks else []
        ),
        *_conservation_lines(p.numeric_conservation),
        *(
            ["Cell evidence: " + ", ".join(
                f"{status}={count}" for status, count in sorted(p.table_cell_evidence.items())
            ) + "."]
            if p.table_cell_evidence else []
        ),
        *(
            ["Resolved values: " + ", ".join(
                f"{confidence}={count}"
                for confidence, count in sorted(p.table_cell_resolution.items())
            ) + "."]
            if p.table_cell_resolution else []
        ),
        *(
            [f"Glyph-verified table cells (born-digital): " + ", ".join(
                f"{verdict}={count}" for verdict, count in sorted(g.items())
            ) + "."]
            if (g := p.table_cell_glyph_check) else []
        ),
        *(
            [f"Equation render-back check: " + ", ".join(
                f"{verdict}={count}" for verdict, count in sorted(r.items())
            ) + "."]
            if (r := p.equation_render_check) else []
        ),
        *(
            [f"Equation LaTeX renders under the bundled math renderer: "
             + ", ".join(f"{k}={v}" for k, v in sorted(s.items())) + "."]
            if (s := p.equation_render_support) else []
        ),
        *(["See `review.md` for the sorted queue and `review.json` for exact records."]
          if sum(review_counts.values()) else []),
        "",
        "## Contents",
        "",
        f"{p.blocks} blocks across {p.pages} pages: {inv}."
        + (f" {p.illegible_blocks} block(s) remained illegible." if p.illegible_blocks else "")
        + (f" {p.ocr_pages} page(s) were scanned (OCR text)." if p.ocr_pages else ""),
        *(
            [f"Retrieval passages: {passage_count}; see `passages.jsonl` and "
             "`passages.schema.json`."]
            if passage_count is not None else []
        ),
        "",
        "## Text sufficiency",
        "",
        f"{p.text_sufficient} of {p.text_sufficient + p.pixel_authoritative} elements are "
        "text-sufficient: readable, searchable, and reconstructable from the markdown alone."
        + (
            f" {p.pixel_authoritative} pixel-authoritative (the image crop is the record): "
            + ", ".join(f"{kind} ({n})" for kind, n in p.pixel_authoritative_by.items())
            + ". Deleting `assets/` loses those."
            if p.pixel_authoritative else " Nothing depends on the crops."
        ),
        "",
        *_run_summary_lines(run_metrics),
        "## Where to start",
        "",
        f"Open [`{contents}`]({contents})"
        + (" for the linked contents tree." if contents == "index.md" else " for the document."),
        "`manifest.json` is the compact machine entry point. `outline.json` maps "
        "sections, files, passage ranges, review hotspots, and source-dependent regions. "
        "`symbols.json` contains only source-quoted local symbol definitions. "
        "`profile.json` has the full quality summary, and `provenance.json` has "
        "block-level lineage.",
        "`review.md` puts likely errors before valid image-dependent entries.",
        "Use contextualized `passages.jsonl` for retrieval and `chunks.jsonl` for "
        "page-local citation evidence without loading the full Markdown.",
        "Image-backed equations and cropped figures keep the image as the authoritative "
        "source; any `[pdf2md: ...]` marker flags something to verify against it.",
        *([f"Each of the {p.ocr_pages} scanned page(s) links its full-page image "
           "(`[page N scan]`) so the OCR text can be checked against the original."]
          if p.ocr_pages else []),
        "",
    ]
    path = version_dir / "README.md"
    path.write_text("\n".join(lines))
    return path
