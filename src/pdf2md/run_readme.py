"""The run summary a person reads: `README.md` inside the bundle.

`profile.py` builds the numbers; this says them to someone. That split matters
more than it looks, because the same measurement reads differently depending on
what the sentence around it claims. `Equation text coverage: none (0/11)` is a
true ratio and a false impression — every one of those equations carries LaTeX,
and the row counts only the ones whose text stands without the crop. Several
functions here exist purely to say what a number does *not* mean.

The other rule: a row that reports a severity reports its mass too. One high
finding saturates a document, so a nine-page paper with one reading-order glitch
read exactly like a 346-page supplement with contaminated data cells until the
breakdown was shown beside it.
"""

from __future__ import annotations

from pathlib import Path

from pdf2md.logging import _duration
from pdf2md.run_metrics import failed_optional_calls
from pdf2md.schema import Document, DocumentProfile

# When the remedy line applies: mostly scanned, enough tables to judge, and
# almost none of them verified.
_SCANNED_SHARE = 0.5

_MIN_TABLES_FOR_REMEDY = 4

_UNVERIFIED_TABLE_SHARE = 0.25

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
        if ratio is not None:
            return f"{dimension['status']} ({dimension['numerator']}/{dimension['denominator']})"
        # A severity is the worst item present, so one reading-order glitch in a
        # reference list reads exactly like a 346-page supplement with contaminated
        # data cells, and nothing ranks across a stack of papers. The breakdown is
        # already counted; showing it is what makes the row triageable.
        counts = dimension.get("counts") or {}
        if {"high", "medium", "low"} <= counts.keys():
            parts = ", ".join(f"{counts[k]} {k}" for k in ("high", "medium", "low") if counts[k])
            total = counts.get("action_required", 0)
            if parts:
                return f"{dimension['status']} ({parts}; {total} action items)"
        return str(dimension["status"])

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
        *_scanned_table_remedy(dimensions),
        *_equation_coverage_note(profile, dimensions),
    ]

def _equation_coverage_note(profile: DocumentProfile, dimensions: dict) -> list[str]:
    """Say what a low equation row means, because the two causes are opposite.

    `Equation text coverage: none (0/11)` reads as "no equation was extracted",
    and on a formula-enabled document that is never what it means: the row counts
    only equations whose text alone is usable, so a scan whose every equation
    carries LaTeX under an authoritative crop scores zero. Measured over the
    corpus, every formula-enabled document transcribes 100% of its equations --
    11 of 11, 194 of 194, 66 of 66 -- and what varies is how many the text layer
    could confirm. The other cause is real and needs the opposite sentence:
    with enrichment off nothing is transcribed at all."""
    dimension = dimensions.get("equation_text_coverage") or {}
    total = dimension.get("denominator") or 0
    usable = dimension.get("numerator") or 0
    if not total or usable == total:
        return []
    if not profile.equations_transcribed:
        return [
            f"None of the {total} equation(s) were transcribed, because formula "
            "enrichment was off (`--no-formula`). Each one is cropped and its image "
            "is the record; re-run without that flag for LaTeX.",
            "",
        ]
    return [
        f"{profile.equations_transcribed} of {total} equation(s) carry LaTeX; the row "
        f"counts only those whose text stands on its own, and {total - usable} "
        f"{'is' if total - usable == 1 else 'are'} image-backed. For those the source crop is authoritative and the LaTeX rides "
        "under it, a reading the page's own text layer could not confirm. "
        "`--render-check` judges exactly those: it draws the LaTeX and compares its ink "
        "against the crop, which is evidence the text layer cannot give.",
        "",
    ]

def _scanned_table_remedy(dimensions: dict) -> list[str]:
    """Name the remedy beside the row it explains.

    A reader who sees `Table verification coverage: none (0/82)` on a scan is
    told what is wrong and not what to do about it, though the measurement
    exists: on a 99-page 1972 compilation MinerU recovered 99% of the printed
    grid against Docling's 21%, with 0.6% of value tokens malformed against
    22.9%. The claim is about scans, so the line is withheld unless the document
    is one -- on a born-digital paper the same low coverage means something else
    entirely, and pointing at a different engine would be guesswork."""
    ocr = dimensions.get("ocr_dependence") or {}
    tables = dimensions.get("table_verification_coverage") or {}
    if (ocr.get("ratio") or 0) < _SCANNED_SHARE:
        return []
    if tables.get("denominator", 0) < _MIN_TABLES_FOR_REMEDY:
        return []
    if (tables.get("ratio") or 0) > _UNVERIFIED_TABLE_SHARE:
        return []
    return [
        "Most of this document is scanned and almost none of its tables verified. "
        "A second engine reads a scan's tables better: over ten scanned documents "
        "converted both ways on one machine, MinerU found 217 tables against "
        "Docling's 138, carried a structural finding on 51% of them against 92%, "
        "recovered 12% more clean values with a lower malformed rate (5.3% against "
        "7.7%), and ran in 19 minutes against 31. Re-run with `--engine mineru` "
        "where it is installed, or `--force-ocr` as the fallback. The source crop "
        "beside each table is authoritative either way.",
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
            f"{p.tables_image_only} image-only"
            + (
                f"; {p.tables_structurally_flagged} with structural findings "
                f"(rows merged, dropped, or shifted) — see review.md"
                if p.tables_structurally_flagged else ""
            )
            + "."
        ),
        f"Derived normalized table datasets: {p.derived_table_datasets}.",
        *(
            [f"Embedded-text-layer word recall: {p.glyph_recall_words_matched} of "
             f"{p.glyph_recall_words_total} word(s) across {p.glyph_recall_blocks} block(s)"
             + (f"; {p.glyph_low_recall_blocks} below 90% — check `review.md`."
                if p.glyph_low_recall_blocks else ".")
             + (f" {p.glyph_accent_damaged_blocks} block(s) kept their words but lost "
                f"diacritics to the font decode."
                if p.glyph_accent_damaged_blocks else "")]
            if p.glyph_recall_blocks else []
        ),
        *(
            [f"Reading order: {len(p.reading_order_pages)} page(s) emit blocks out of "
             f"the order the page prints them — see `review.md`."]
            if p.reading_order_pages else []
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
            ["Glyph-verified table cells (born-digital): " + ", ".join(
                f"{verdict}={count}" for verdict, count in sorted(g.items())
            ) + "."]
            if (g := p.table_cell_glyph_check) else []
        ),
        *(
            ["Equation render-back check: " + ", ".join(
                f"{verdict}={count}" for verdict, count in sorted(r.items())
            ) + "."]
            if (r := p.equation_render_check) else []
        ),
        *(
            ["Equation LaTeX renders under the bundled math renderer: "
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
