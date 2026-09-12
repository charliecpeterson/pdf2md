"""The last stage: audit the emitted bundle, write the derived files, seal the version.

Everything here reads what the earlier stages produced and adds no content of its
own. `provenance.json` is written last and atomically, because its presence is
what marks a version complete.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from pdf2md import __version__
from pdf2md.cache import deduplicate_assets, release_claim
from pdf2md.chunks import write_chunks
from pdf2md.conservation import (
    annotate_conservation_warnings,
    conservation_review_flags,
    numeric_conservation,
)
from pdf2md.crops import _placeable
from pdf2md.document_map import write_document_map
from pdf2md.logging import get_logger
from pdf2md.passage_tokenizer import load_passage_tokenizer
from pdf2md.passages import write_passages
from pdf2md.profile import build_profile, write_manifest, write_profile
from pdf2md.reading_order import reading_order_flags
from pdf2md.recall import recall_review_flags
from pdf2md.review import build_review_queue, write_review_files
from pdf2md.run_readme import write_readme
from pdf2md.schema import FORMAT_VERSION, ConvertResult, Provenance
from pdf2md.stages import _Run
from pdf2md.symbol_index import write_symbol_index
from pdf2md.table_artifacts import annotate_table_artifacts

log = get_logger("pipeline")


def _finalize_bundle(run: _Run) -> ConvertResult:
    """Audit, write the derived files, and seal the version."""
    run.progress.stage("writing profile, chunks, and manifest")
    consistency = numeric_conservation(
        run.pdf_path,
        (path.read_text() for path in run.md_files),
        force_ocr=run.config.force_ocr,
        document=run.doc,
        emission_index=run.emission_index,
    )
    conservation_flags = conservation_review_flags(consistency)
    recall_flags, diacritic_flags = recall_review_flags(run.result.blocks)
    order_flags, order_pages = reading_order_flags(
        run.result.blocks, run.emission_index, pdf_path=run.pdf_path, force_ocr=run.config.force_ocr
    )
    run.doc.coverage.flags.extend(
        conservation_flags + recall_flags + order_flags + diacritic_flags
    )
    # Diacritic findings reach review.json and profile.json but not the Markdown:
    # the content is there and mis-spelled, and one marker per accented surname
    # would bury a bibliography.
    annotate_conservation_warnings(
        run.vdir,
        conservation_flags + _placeable(recall_flags + order_flags, run.emission_index),
        run.emission_index,
    )
    annotate_table_artifacts(run.vdir, run.doc, run.doc.coverage.flags)
    review_queue = build_review_queue(run.doc)
    profile = build_profile(
        run.doc,
        consistency=consistency,
        metadata=run.meta,
        engine_quality=run.result.quality_evidence,
        review_queue=review_queue,
        reading_order=order_pages,
    )
    write_review_files(run.vdir, review_queue)
    write_profile(run.vdir, run.doc, profile, run.md_files)
    chunks_path = write_chunks(
        run.vdir,
        run.doc,
        run.md_files,
        run.page_rasters,
        emission_index=run.emission_index,
    )
    passage_tokenizer = load_passage_tokenizer(run.config.passage_tokenizer)
    passages_path, _, passage_count = write_passages(
        run.vdir,
        run.doc,
        run.meta,
        run.md_files,
        run.page_rasters,
        emission_index=run.emission_index,
        tokenizer=passage_tokenizer,
        max_tokens=run.config.passage_max_tokens,
        section_roles=run.section_roles,
    )
    write_document_map(
        run.vdir,
        run.doc,
        run.meta,
        run.md_files,
        passages_path,
        section_roles=run.section_roles,
    )
    write_symbol_index(run.vdir, run.doc.doc_id, passages_path)
    write_manifest(
        run.vdir, run.doc, run.meta, profile, run.md_files, run.page_rasters,
        review_queue=review_queue,
        passage_count=passage_count,
        document_metadata=run.document_metadata,
    )
    with chunks_path.open() as chunks_file:
        chunk_count = sum(1 for _ in chunks_file)
    run.metrics.finish(
        "audit",
        review_flags=profile.review_flags,
        action_required=profile.review_counts.get("action_required", 0),
        source_dependent=profile.review_counts.get("source_dependent", 0),
        informational=profile.review_counts.get("informational", 0),
        chunks=chunk_count,
        passages=passage_count,
    )

    linked_assets, linked_bytes = deduplicate_assets(run.vdir)
    if linked_assets:
        log.info(
            "deduplicated %d unchanged asset(s), %d logical bytes",
            linked_assets,
            linked_bytes,
        )
    run.metrics.finish(
        "finalize",
        deduplicated_assets=linked_assets,
        deduplicated_bytes=linked_bytes,
    )
    run_metrics = run.metrics.report()
    write_readme(
        run.vdir,
        run.doc,
        run.meta,
        profile,
        run.md_files,
        run_metrics=run_metrics,
        passage_count=passage_count,
    )

    finished = datetime.now(UTC)
    run.doc.provenance = Provenance(
        tool_version=__version__,
        engine_versions=run.result.engine_versions,
        format_version=FORMAT_VERSION,
        source_path=str(run.pdf_path),
        source_sha256=run.doc_id,
        page_count=run.doc.page_count,
        started_at=run.started.isoformat(),
        finished_at=finished.isoformat(),
        duration_s=run_metrics["duration_s"],
        section_source=run.structure.section_source,
        derivation=run.derivation,
        run_fingerprint=run.fingerprint,
        run_inputs=run.run_inputs,
        run_metrics=run_metrics,
    )
    # provenance.json is the completion marker (its presence = a finished run), so write
    # it atomically — a truncated marker from a killed process must never look complete.
    prov_path = run.vdir / "provenance.json"
    tmp_prov = prov_path.with_suffix(".json.tmp")
    tmp_prov.write_text(json.dumps(run.doc.to_dict(), indent=2, default=str))
    tmp_prov.replace(prov_path)
    release_claim(run.vdir)

    log.info(
        "converted %s -> v%d (%d md files, %s)",
        run.pdf_path.name, run.version, len(run.md_files),
        "INCOMPLETE ACCOUNTING" if not run.doc.coverage.accounted_for else
        "review required" if run.doc.coverage.needs_review else "complete",
    )
    return ConvertResult(
        run.doc_id,
        run.version,
        run.vdir,
        run.md_files,
        coverage=run.doc.coverage,
        page_count=run.doc.page_count,
        run_metrics=run_metrics,
    )
