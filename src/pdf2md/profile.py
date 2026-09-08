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
from pdf2md.grobid import REFS_TEI_NAME
from pdf2md.quality import build_quality_scorecard
from pdf2md.recall import recall_summary
from pdf2md.review import build_review_queue
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
    reading_order: dict | None = None,
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
    # A table can be text-backed cell by cell and still be missing whole rows,
    # so this is counted beside `tables_verified` rather than folded into it.
    tables_structurally_flagged = sum(
        1 for table in doc.tables if table.grid_audit.get("findings")
    )
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
        tables_structurally_flagged=tables_structurally_flagged,
        tables_image_only=tables_image_only,
        derived_table_datasets=derived_table_datasets,
        table_cell_evidence=dict(table_cell_evidence),
        table_cell_resolution=dict(table_cell_resolution),
        table_cell_glyph_check=glyph_check,
        equation_render_check=dict(render_check),
        equation_render_support=dict(render_support),
        equations=len(eqs),
        equations_image_backed=image_backed,
        equations_transcribed=sum(1 for block in eqs if block.text.strip()),
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
        glyph_accent_damaged_blocks=recall["accent_damaged_blocks"],
        reading_order_pages=dict(reading_order or {}),
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














