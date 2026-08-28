"""Build independent document-quality dimensions from conversion evidence."""

from __future__ import annotations

from pdf2md.confidence import plot_data_accepted
from pdf2md.schema import BlockType, Document


def _ratio(
    status: str,
    numerator: int,
    denominator: int,
    evidence_source: str,
    *,
    note: str | None = None,
) -> dict:
    dimension = {
        "status": status,
        "ratio": round(numerator / denominator, 4) if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
        "evidence_source": evidence_source,
        "calibrated": False,
    }
    if note:
        dimension["note"] = note
    return dimension


def _coverage_dimensions(doc: Document) -> tuple[dict, dict]:
    coverage = doc.coverage
    if not coverage:
        unavailable = {
            "status": "not_measured",
            "evidence_source": "coverage audit was not run",
            "calibrated": False,
        }
        return unavailable, dict(unavailable)
    dispositions = coverage.emitted + coverage.cropped + coverage.flagged + coverage.dropped
    accounting = _ratio(
        "complete" if coverage.accounted_for else "incomplete",
        dispositions,
        coverage.total_blocks,
        "coverage audit dispositions",
    )
    represented = coverage.emitted + coverage.cropped + coverage.flagged
    structural = _ratio(
        "complete" if coverage.accounted_for and coverage.dropped == 0 else "incomplete",
        represented,
        coverage.total_blocks,
        "coverage audit represented and dropped block counts",
        note="Image and text representations both count as structurally present.",
    )
    return accounting, structural


def _text_dimension(text_sufficient: int, pixel_authoritative: int) -> dict:
    total = text_sufficient + pixel_authoritative
    status = (
        "not_applicable" if not total else
        "full" if text_sufficient == total else
        "partial" if text_sufficient else "none"
    )
    return _ratio(
        status,
        text_sufficient,
        total,
        "element sufficiency audit",
        note="The ratio measures usable text or data, not semantic correctness.",
    )


def _engine_dimensions(doc: Document, engine_quality: dict | None) -> tuple[dict, dict, dict]:
    grades = (engine_quality or {}).get("grades", {})
    source = (engine_quality or {}).get("source", "conversion engine")
    layout_grade = grades.get("layout")
    layout = {
        "status": layout_grade or "not_measured",
        "evidence_source": source if layout_grade else "no native layout grade retained",
        "calibrated": False,
        "note": "An engine-native grade, not a calibrated probability." if layout_grade else (
            "Block accounting does not measure reading order or region-boundary accuracy."
        ),
    }
    ocr_pages = len({block.page for block in doc.blocks if block.extra.get("ocr")})
    dependence_status = (
        "not_applicable" if not doc.page_count else
        "none" if not ocr_pages else
        "full" if ocr_pages == doc.page_count else "partial"
    )
    dependence = _ratio(
        dependence_status,
        ocr_pages,
        doc.page_count,
        "block OCR markers grouped by source page",
    )
    ocr_grade = grades.get("ocr")
    quality = {
        "status": ocr_grade or ("not_applicable" if not ocr_pages else "not_measured"),
        "evidence_source": source if ocr_grade else (
            "no OCR used" if not ocr_pages else "no native OCR grade retained"
        ),
        "calibrated": False,
        "note": "An engine-native grade, not a calibrated probability." if ocr_grade else (
            "OCR output has no reference transcription in this conversion."
        ),
    }
    return layout, dependence, quality


def _content_dimensions(
    doc: Document,
    equations_text: int,
    tables_verified: int,
) -> tuple[dict, dict, dict]:
    equation_total = sum(block.type is BlockType.EQUATION for block in doc.blocks)
    equation_status = (
        "not_applicable" if not equation_total else
        "full" if equations_text == equation_total else
        "partial" if equations_text else "none"
    )
    equation = _ratio(
        equation_status,
        equations_text,
        equation_total,
        "equation block text, coverage disposition, and authoritative-crop markers",
        note="Only usable equation text counts; an image-backed equation remains structurally present.",
    )
    table_total = len(doc.tables)
    table_status = (
        "not_applicable" if not table_total else
        "full" if tables_verified == table_total else
        "partial" if tables_verified else "none"
    )
    table = _ratio(
        table_status,
        tables_verified,
        table_total,
        "structured table content and OCR/cell-verification markers",
        note="Docling's native table score is excluded because it is not implemented.",
    )
    figure_text = sum(bool(figure.caption or figure.description or figure.labels) for figure in doc.figures)
    figure_data = sum(plot_data_accepted(figure.digitization) for figure in doc.figures)
    figure_supported = sum(
        bool(figure.caption or figure.description or figure.labels
             or plot_data_accepted(figure.digitization))
        for figure in doc.figures
    )
    figure_total = len(doc.figures)
    figure_status = (
        "not_applicable" if not figure_total else
        "full" if figure_supported == figure_total else
        "partial" if figure_supported else "none"
    )
    figure = _ratio(
        figure_status,
        figure_supported,
        figure_total,
        "figure captions, descriptions, printed labels, and accepted digitizations",
    )
    figure["figures_with_text"] = figure_text
    figure["figures_with_accepted_data"] = figure_data
    return equation, table, figure


def _metadata_dimension(metadata: dict | None) -> dict:
    selected = metadata or {}
    present = [
        field for field in ("title", "authors", "year", "doi", "venue")
        if selected.get(field)
    ]
    evidence = selected.get("metadata_evidence", {})
    field_quality = {
        field: record["selected"]["quality"] if record.get("selected") else "missing"
        for field, record in evidence.items()
        if field not in {"schema_version", "method"} and isinstance(record, dict)
    }
    if metadata is None:
        status = "not_measured"
        source = "metadata was not supplied to the profile builder"
    else:
        status = (
            "identified" if selected.get("title") and len(present) > 1 else
            "partial" if present else "missing"
        )
        source = (
            "ranked local metadata evidence and optional GROBID enrichment"
            if evidence else
            "embedded PDF metadata, first-page heuristics, and optional GROBID enrichment"
        )
    return {
        "status": status,
        "present_fields": present,
        "field_quality": field_quality,
        "evidence_source": source,
        "calibrated": False,
        "note": "Field presence is reported; bibliographic correctness is not independently verified.",
    }


def _unresolved_dimension(doc: Document, review_queue: dict | None) -> dict:
    coverage = doc.coverage
    action_items = [
        item for item in (review_queue or {}).get("items", [])
        if item["disposition"] == "action_required"
    ]
    if review_queue is not None:
        counts = {
            "action_required": len(action_items),
            "high": sum(item["severity"] == "high" for item in action_items),
            "medium": sum(item["severity"] == "medium" for item in action_items),
            "low": sum(item["severity"] == "low" for item in action_items),
        }
        status = (
            "high" if counts["high"] else
            "medium" if counts["medium"] else
            "low" if counts["low"] else "none"
        )
    elif not coverage:
        status, counts = "unknown", {}
    else:
        dispositions = coverage.emitted + coverage.cropped + coverage.flagged + coverage.dropped
        counts = {
            "unaccounted_blocks": abs(coverage.total_blocks - dispositions),
            "dropped_blocks": coverage.dropped,
            "illegible_prose_blocks": coverage.illegible,
            "other_flagged_blocks": max(0, coverage.flagged - coverage.illegible),
        }
        status = (
            "high" if counts["unaccounted_blocks"] or counts["dropped_blocks"]
            or counts["illegible_prose_blocks"] else
            "medium" if counts["other_flagged_blocks"] else "none"
        )
    return {
        "status": status,
        "counts": counts,
        "evidence_source": "coverage audit unresolved dispositions and legibility flags",
        "calibrated": False,
    }


def build_quality_scorecard(
    doc: Document,
    *,
    text_sufficient: int,
    pixel_authoritative: int,
    equations_text: int,
    tables_verified: int,
    metadata: dict | None,
    engine_quality: dict | None,
    review_queue: dict | None,
) -> dict:
    accounting, structural = _coverage_dimensions(doc)
    layout, ocr_dependence, ocr_quality = _engine_dimensions(doc, engine_quality)
    equation, table, figure = _content_dimensions(doc, equations_text, tables_verified)
    return {
        "schema_version": 1,
        "dimensions": {
            "accounting_coverage": accounting,
            "structural_completeness": structural,
            "text_sufficiency": _text_dimension(text_sufficient, pixel_authoritative),
            "layout_quality": layout,
            "ocr_dependence": ocr_dependence,
            "ocr_quality": ocr_quality,
            "equation_text_coverage": equation,
            "table_verification_coverage": table,
            "figure_text_data_coverage": figure,
            "metadata_quality": _metadata_dimension(metadata),
            "unresolved_error_severity": _unresolved_dimension(doc, review_queue),
        },
        "engine_evidence": engine_quality or {
            "source": "conversion engine",
            "calibrated": False,
            "grades": {},
            "note": "No native engine quality grades were retained.",
        },
    }
