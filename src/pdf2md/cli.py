"""Typer CLI. Thin over the library; the only place a logging handler is installed."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import typer

from pdf2md import __version__
from pdf2md.cache import content_hash, doc_dir, document_dirs, latest_version, out_root
from pdf2md.config import Config
from pdf2md.logging import _duration, configure_cli_logging
from pdf2md.pipeline import ConvertResult, convert_dir, convert_file
from pdf2md.run_metrics import failed_optional_calls
from pdf2md.search import find_passages

app = typer.Typer(help="Auditable PDF to markdown converter.", no_args_is_help=True)
models_app = typer.Typer(help="Manage conversion models.")
line_reader_app = typer.Typer(help="Attach optional PP-OCRv6 table-key evidence.")
app.add_typer(models_app, name="models")
app.add_typer(line_reader_app, name="line-reader")


def _load_config(path: Path | None) -> Config:
    try:
        return Config.load(path) if path else Config()
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="--config") from exc


def _replace_config(config: Config, **changes) -> Config:
    try:
        return replace(config, **changes)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def convert(
    path: Path = typer.Argument(..., exists=True, help="A PDF file or a directory of PDFs."),
    out: Path = typer.Option(
        None, "--out", "-o", help="Output root (default ./out).",
        rich_help_panel="Input and output",
    ),
    config: Path = typer.Option(
        None, "--config", "-c", exists=True, help="TOML config.",
        rich_help_panel="Input and output",
    ),
    passage_tokenizer: str = typer.Option(
        None, "--passage-tokenizer",
        help="Passage counter: lexical (offline) or hf:<model-or-local-path>.",
        rich_help_panel="Input and output",
    ),
    passage_max_tokens: int = typer.Option(
        None, "--passage-max-tokens", min=1,
        help="Maximum tokens per contextualized retrieval passage.",
        rich_help_panel="Input and output",
    ),
    engine: str = typer.Option(
        None, "--engine", help="Parser backend: docling (default) or mineru.",
        rich_help_panel="Input and output",
    ),
    mineru_executable: str = typer.Option(
        None, "--mineru-executable",
        help="Path to the MinerU CLI when --engine mineru uses a separate environment.",
        rich_help_panel="Input and output",
    ),
    table_ocr_executable: str = typer.Option(
        None, "--table-ocr-executable",
        help="Independently re-read table crops with Tesseract and emit cell-level comparisons.",
        rich_help_panel="Verification",
    ),
    table_reference: Path = typer.Option(
        None, "--table-reference", exists=True,
        help="Reference CSV with atomic_number,row_key,column,value for external checks.",
        rich_help_panel="Verification",
    ),
    grobid_url: str = typer.Option(
        None, "--grobid-url",
        help="Enrich bibliographic metadata (title/authors/abstract/DOI/references) "
             "from a running GROBID service (e.g. http://localhost:8070). Unreachable "
             "degrades to the embedded-metadata heuristics with a warning.",
        rich_help_panel="Verification",
    ),
    metadata_online: bool = typer.Option(
        False,
        "--metadata-online",
        help="Resolve a locally extracted DOI to CSL-JSON and retain the registry record.",
        rich_help_panel="Verification",
    ),
    render_check: bool = typer.Option(
        False, "--render-check",
        help="Render each image-backed equation's LaTeX and compare ink layout "
             "against its source crop as evidence tiers (needs the eqrender extra: "
             "uv sync --extra eqrender).",
        rich_help_panel="Equations",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Re-convert even if cached.",
        rich_help_panel="Input and output",
    ),
    no_formula: bool = typer.Option(
        False, "--no-formula", help="Skip formula→LaTeX enrichment (much faster; for books/scans).",
        rich_help_panel="Equations",
    ),
    no_scripts: bool = typer.Option(
        False, "--no-scripts", help="Skip inline sub/superscript recovery (faster on large docs).",
        rich_help_panel="Equations",
    ),
    no_deskew: bool = typer.Option(
        False, "--no-deskew",
        help="Skip conservative fine-deskewing of textless pages before MinerU OCR.",
        rich_help_panel="Scans and OCR",
    ),
    force_ocr: bool = typer.Option(
        False, "--force-ocr",
        help="Re-OCR page images instead of trusting the embedded text layer — for a PDF whose "
             "own text is bad OCR. Pair with --ocr-page-vlm for a full-page vision read.",
        rich_help_panel="Scans and OCR",
    ),
    transcribe: bool = typer.Option(
        False, "--transcribe",
        help="Re-transcribe image-backed equations with local math-OCR (needs surya-ocr; slow).",
        rich_help_panel="Equations",
    ),
    describe: bool = typer.Option(
        False, "--describe",
        help="Describe figure/table/equation crops with a vision model over an "
             "OpenAI-compatible API (needs the `describe` extra + a reachable endpoint; slow).",
        rich_help_panel="Vision models",
    ),
    ocr_page_vlm: bool = typer.Option(
        False, "--ocr-page-vlm",
        help="Transcribe each scanned page whole with the vision model (one call per page; sees "
             "full layout/tables but collapses element structure). Needs the describe extra + endpoint. "
             "Use an OCR-tuned model (--vlm-ocr-model glm-ocr) — fast and exact; general VLMs are "
             "minutes-per-page or unreliable here.",
        rich_help_panel="Scans and OCR",
    ),
    vlm_model: str = typer.Option(
        None, "--vlm-model", help="Vision model for --describe figures (overrides config).",
        rich_help_panel="Vision models",
    ),
    vlm_ocr_model: str = typer.Option(
        None, "--vlm-ocr-model",
        help="OCR-tuned model for --describe tables/equations (e.g. glm-ocr); defaults to --vlm-model.",
        rich_help_panel="Vision models",
    ),
    no_digitize: bool = typer.Option(
        False, "--no-digitize",
        help="Skip vector-chart data recovery (on by default; near-lossless, no model). "
             "Born-digital charts otherwise ship their data + a repro script, not just a crop.",
        rich_help_panel="Figures and data",
    ),
    digitize_vlm: bool = typer.Option(
        False, "--digitize-vlm",
        help="Tier 2: also estimate data from raster/scanned plots with a vision model "
             "(needs the describe extra + endpoint; approximate, low confidence).",
        rich_help_panel="Figures and data",
    ),
    digitize_consensus: int = typer.Option(
        None, "--digitize-consensus", min=1,
        help="Sample --digitize-vlm N times per raster figure and keep the per-bin "
             "median curve, scaling confidence by across-draw dispersion (one extra "
             "model call per vote).",
        rich_help_panel="Figures and data",
    ),
    figure_labels: bool = typer.Option(
        False, "--figure-labels",
        help="Read the printed labels off each figure (axis titles, peak/data labels, "
             "legend) with a vision model (needs the describe extra + endpoint).",
        rich_help_panel="Figures and data",
    ),
    figure_svg: bool = typer.Option(
        False, "--figure-svg",
        help="Also export each born-digital figure as SVG (lossless vector text form; "
             "needs pdftocairo from poppler on PATH). Scanned pages stay PNG-only.",
        rich_help_panel="Figures and data",
    ),
    ocr_consensus: int = typer.Option(
        None, "--ocr-consensus", min=1,
        help="Re-read each figure under --figure-labels N times and lower confidence "
             "when the reads disagree (costs a model call per vote).",
        rich_help_panel="Vision models",
    ),
    no_page_images: bool = typer.Option(
        False, "--no-page-images",
        help="Skip the per-page verification raster for scanned pages (saves disk on a "
             "long scanned book; born-digital docs are unaffected either way).",
        rich_help_panel="Scans and OCR",
    ),
    page_images_all: bool = typer.Option(
        False, "--page-images-all",
        help="Capture a full-page image for EVERY page, not just scanned ones "
             "(page-faithful capture: any answer can be checked against the source "
             "image). Costs roughly 100-300 KB of disk per page.",
        rich_help_panel="Scans and OCR",
    ),
    no_figure_ocr: bool = typer.Option(
        False, "--no-figure-ocr",
        help="Skip the model-free upright re-OCR of scanned figures (on by default; recovers "
             "a sideways scan's axis labels). Born-digital figures are unaffected.",
        rich_help_panel="Scans and OCR",
    ),
    no_word_split: bool = typer.Option(
        False, "--no-word-split",
        help="Skip re-splitting run-together OCR words in scanned prose (on by default; "
             "'wherethefirst' -> 'where the first'). Turn off for a scanned non-English doc.",
        rich_help_panel="Scans and OCR",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", rich_help_panel="Input and output",
    ),
) -> None:
    """Convert a PDF (or every PDF in a directory) to markdown."""
    configure_cli_logging(verbose)
    if out:
        os.environ["PDF2MD_OUT"] = str(out)
    cfg = _load_config(config)
    if engine:
        cfg = _replace_config(cfg, engine=engine)
    if passage_tokenizer:
        cfg = _replace_config(cfg, passage_tokenizer=passage_tokenizer)
    if passage_max_tokens:
        cfg = _replace_config(cfg, passage_max_tokens=passage_max_tokens)
    if mineru_executable:
        cfg = _replace_config(cfg, mineru_executable=mineru_executable)
    if table_ocr_executable:
        cfg = _replace_config(cfg, table_ocr_executable=table_ocr_executable)
    if table_reference:
        cfg = _replace_config(cfg, table_reference_path=str(table_reference))
    if grobid_url:
        cfg = _replace_config(cfg, grobid_url=grobid_url)
    if metadata_online:
        cfg = _replace_config(cfg, doi_metadata=True)
    if render_check:
        cfg = _replace_config(cfg, check_equation_render=True)
    if digitize_consensus:
        cfg = _replace_config(cfg, digitize_consensus_votes=digitize_consensus)
    if no_formula:
        cfg = _replace_config(cfg, do_formula_enrichment=False)
    if force_ocr:
        cfg = _replace_config(cfg, force_ocr=True)
    if no_scripts:
        cfg = _replace_config(cfg, detect_scripts=False)
    if no_deskew:
        cfg = _replace_config(cfg, deskew_scans=False)
    if transcribe:
        cfg = _replace_config(cfg, transcribe_equations=True)
    if describe:
        cfg = _replace_config(cfg, describe_figures=True)
    if ocr_page_vlm:
        cfg = _replace_config(cfg, ocr_page_vlm=True)
    if vlm_model:
        cfg = _replace_config(cfg, vlm_model=vlm_model)
    if vlm_ocr_model:
        cfg = _replace_config(cfg, vlm_ocr_model=vlm_ocr_model)
    if no_digitize:
        cfg = _replace_config(cfg, digitize_figures=False)
    if digitize_vlm:
        cfg = _replace_config(cfg, digitize_vlm=True)
    if figure_labels:
        cfg = _replace_config(cfg, figure_labels=True)
    if figure_svg:
        cfg = _replace_config(cfg, figure_svg=True)
    if ocr_consensus:
        cfg = _replace_config(cfg, ocr_consensus_votes=ocr_consensus)
    if no_page_images:
        cfg = _replace_config(cfg, page_images=False)
    if page_images_all:
        cfg = _replace_config(cfg, page_images_all_pages=True)
    if no_figure_ocr:
        cfg = _replace_config(cfg, ocr_figures=False)
    if no_word_split:
        cfg = _replace_config(cfg, resegment_ocr=False)

    if path.is_dir():
        results = convert_dir(path, config=cfg, force=force)
    else:
        results = [convert_file(path, config=cfg, force=force)]

    _report(results)
    if any(r.failed for r in results):
        raise typer.Exit(1)


@app.command()
def enrich(
    document: Path = typer.Argument(
        ...,
        exists=True,
        help="Source PDF, document directory, or completed v<n> bundle.",
    ),
    equations: bool = typer.Option(
        False,
        "--equations",
        help="Re-transcribe image-backed equations with local math OCR.",
    ),
    charts: bool = typer.Option(
        False,
        "--charts",
        help="Recover chart data, including model-assisted raster charts.",
    ),
    descriptions: bool = typer.Option(
        False,
        "--descriptions",
        help="Describe figure, table, and equation crops with the configured vision model.",
    ),
    metadata: bool = typer.Option(
        False,
        "--metadata",
        help="Resolve the locally extracted DOI and retain its CSL-JSON metadata.",
    ),
    out: Path = typer.Option(
        None,
        "--out",
        "-o",
        help="Output root used to locate a source PDF's conversion.",
    ),
    config: Path = typer.Option(
        None,
        "--config",
        "-c",
        exists=True,
        help="TOML overrides applied to the source version's effective configuration.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print eligible region and existing-evidence counts without running enrichment.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Add optional evidence to a completed bundle without rerunning its parser."""
    from pdf2md.enrichment import (
        config_from_version,
        enrich_version,
        preflight,
        resolve_version,
    )

    configure_cli_logging(verbose)
    stages = tuple(
        name
        for name, enabled in (
            ("equations", equations),
            ("charts", charts),
            ("descriptions", descriptions),
            ("metadata", metadata),
        )
        if enabled
    )
    try:
        version_dir = resolve_version(document, output_root=out)
        plan = preflight(version_dir, stages)
        cfg = config_from_version(version_dir, config)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(str(exc), param_hint="document") from exc

    typer.echo(
        f"preflight: {plan.source_version.name}, {plan.pages} pages; "
        f"stages: {', '.join(plan.stages)}"
    )
    work = []
    if equations:
        work.append(
            f"{plan.equation_regions} image-backed equations "
            f"({plan.equation_transcriptions} already transcribed)"
        )
    if charts:
        work.append(
            f"{plan.figures} figures ({plan.chart_datasets} already have data; "
            f"up to {plan.chart_model_candidates} model candidates)"
        )
    if descriptions:
        work.append(
            f"{plan.description_regions} eligible crop descriptions "
            f"({plan.descriptions_present} already present)"
        )
    if metadata:
        registry_state = (
            "registry record present"
            if plan.registry_metadata_present
            else "registry lookup pending"
        )
        work.append(
            f"DOI {plan.doi or 'not yet identified'} "
            f"({registry_state})"
        )
    typer.echo(f"  regions: {', '.join(work)}")
    typer.echo("  output: new immutable version; completed region results use the document cache")
    if plan.pages >= 200:
        typer.echo(
            "  large document: model-backed stages may take hours; run one stage at a time "
            "if you want separate checkpoints"
        )
    if dry_run:
        typer.echo("dry run: no version or cache files written")
        return

    try:
        result = enrich_version(version_dir, stages, config=cfg)
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(f"FAILED  {exc}")
        typer.echo(f"  source bundle unchanged: {version_dir}")
        raise typer.Exit(1) from exc
    _report([result])
    if result.failed:
        raise typer.Exit(1)


@app.command()
def coverage(
    path: Path = typer.Argument(..., exists=True, help="A previously converted PDF."),
    out: Path = typer.Option(None, "--out", "-o", help="Output root used for conversion."),
) -> None:
    """Print the coverage report for an already-converted PDF (no re-run)."""
    doc_id = content_hash(path)
    dd = doc_dir(doc_id, path, root=out) if out else doc_dir(doc_id, path)
    version = latest_version(dd)
    if version is None:
        typer.echo(f"not converted yet: {path}")
        raise typer.Exit(1)
    version_dir = dd / f"v{version}"
    prov = json.loads((version_dir / "provenance.json").read_text())
    cov = prov.get("coverage") or {}
    flags = cov.get("flags") or []
    total = int(cov.get("total_blocks", 0))
    emitted = int(cov.get("emitted", 0))
    cropped = int(cov.get("cropped", 0))
    flagged_blocks = int(cov.get("flagged", 0))
    dropped = int(cov.get("dropped", 0))
    accounted_for = cov.get(
        "accounted_for",
        total == emitted + cropped + flagged_blocks + dropped,
    )
    complete = cov.get(
        "complete",
        bool(accounted_for and flagged_blocks == 0 and dropped == 0),
    )
    review_counts = {
        disposition: sum(
            flag.get("disposition", "action_required") == disposition
            for flag in flags
        )
        for disposition in ("action_required", "source_dependent", "informational")
    }
    report = {
        "source": str(path.resolve()),
        "output": str(version_dir.resolve()),
        "version": version,
        "accounting": {
            "total_blocks": total,
            "emitted": emitted,
            "cropped": cropped,
            "flagged_blocks": flagged_blocks,
            "dropped": dropped,
            "illegible": int(cov.get("illegible", 0)),
            "accounted_for": bool(accounted_for),
            "complete": bool(complete),
        },
        "review": {
            "required": bool(review_counts["action_required"]) or not complete,
            "flag_count": len(flags),
            "counts": review_counts,
            "flags": flags,
        },
    }
    typer.echo(json.dumps(report, indent=2))


@app.command("compare-runs")
def compare_runs(
    before: Path = typer.Argument(..., exists=True, file_okay=False),
    after: Path = typer.Argument(..., exists=True, file_okay=False),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Compare stored stage timings and work counts from two completed versions."""
    from pdf2md.run_metrics import compare_run_metrics, load_run_metrics

    try:
        comparison = compare_run_metrics(
            load_run_metrics(before), load_run_metrics(after)
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(comparison, indent=2))
        return

    total = comparison["duration"]
    percent = total["change_percent"]
    change = "unavailable" if percent is None else f"{percent:+.1f}%"
    typer.echo(
        f"total  {_duration(total['before_s'])} -> {_duration(total['after_s'])} "
        f"({change})"
    )
    if comparison["memory"]:
        memory = comparison["memory"]["main_process_peak_rss_bytes"]
        percent = memory["change_percent"]
        change = "unavailable" if percent is None else f"{percent:+.1f}%"
        typer.echo(
            f"memory {memory['before_bytes'] / 1024**2:,.1f} MiB -> "
            f"{memory['after_bytes'] / 1024**2:,.1f} MiB ({change})"
        )
    for stage in comparison["stages"]:
        percent = stage["change_percent"]
        change = "unavailable" if percent is None else f"{percent:+.1f}%"
        typer.echo(
            f"{stage['stage']:<13} {_duration(stage['before_s']):>9} -> "
            f"{_duration(stage['after_s']):<9} {change:>12}"
        )


@app.command("list")
def list_documents(
    out: Path = typer.Option(None, "--out", "-o", help="Output library (default ./out)."),
) -> None:
    """List converted documents and their latest completed versions."""
    root = out.expanduser().resolve() if out else out_root()
    documents = document_dirs(root, recursive=True)
    if not documents:
        typer.echo(f"no converted documents under {root}")
        return

    for document in documents:
        version = latest_version(document)
        if version is None:
            continue
        version_dir = document / f"v{version}"
        profile = _read_profile(version_dir)
        accounted_for = profile.get("accounted_for")
        review_counts = profile.get("review_counts") or {
            "action_required": profile.get("review_flags", 0)
            if profile.get("needs_review", False) else 0
        }
        status = (
            "INCOMPLETE" if accounted_for is False else
            "REVIEW" if review_counts.get("action_required", 0) else
            "complete" if accounted_for is True else
            "unknown"
        )
        source = profile.get("source") or document.name
        fields = _read_document_fields(version_dir)
        title = fields.get("title") or source
        pages = profile.get("pages", "?")
        markers = profile.get("review_flags", 0)
        contents = profile.get("contents")
        if contents:
            content = version_dir / contents
        else:
            markdown = sorted(version_dir.glob("*.md"))
            content = markdown[0] if markdown else version_dir
        identity = []
        authors = fields.get("authors")
        if authors:
            identity.append("authors: " + "; ".join(str(author) for author in authors))
        if fields.get("year"):
            identity.append(f"year: {fields['year']}")
        if fields.get("doi"):
            identity.append(f"DOI: {fields['doi']}")
        lines = [title]
        if identity:
            lines.append("  " + " | ".join(identity))
        if title != source:
            lines.append(f"  source: {source}")
        lines.extend([
            f"  v{version}  [{status}]  {pages} pages  {markers} review markers",
            f"  content: {content}",
        ])
        typer.echo("\n".join(lines))


@app.command("find")
def find_text(
    target: Path = typer.Argument(
        ...,
        exists=True,
        help="Source PDF, document directory, completed bundle, or output library.",
    ),
    query: str = typer.Argument(..., help="Literal phrase to find in passage text."),
    out: Path = typer.Option(
        None,
        "--out",
        "-o",
        help="Output root used to locate a converted source PDF.",
    ),
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum matches to print."),
) -> None:
    """Find a phrase with page, section, authority, and review status."""
    try:
        matches = find_passages(target, query, output_root=out, limit=limit)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="target") from exc
    if not matches:
        typer.echo(f"no matches for {query!r}")
        raise typer.Exit(1)

    for match in matches:
        review = (
            ",".join(item.upper() for item in match.review_dispositions)
            if match.review_dispositions
            else "clear"
        )
        section = f"  §{match.section}" if match.section else ""
        typer.echo(
            f"{match.title}\n"
            f"  p{match.page}{section}  authority={match.authority}  review={review}\n"
            f"  \"{match.excerpt}\"\n"
            f"  {match.source}"
        )
    typer.echo(f"{len(matches)} match{'es' if len(matches) != 1 else ''}")


@app.command("review-tables")
def review_tables(
    version_dir: Path = typer.Argument(
        ..., exists=True, file_okay=False,
        help="Completed output version, such as out/<document>/v5.",
    ),
    output: Path = typer.Option(
        None, "--output", "-o", help="Review HTML path (default VERSION_DIR/table-review.html)."
    ),
    sample: int = typer.Option(
        90, "--sample", min=1, help="Number of numeric cells to sample."
    ),
    seed: int = typer.Option(0, "--seed", help="Deterministic sampling seed."),
    per_table: int = typer.Option(
        3, "--per-table", min=1, help="Initial sample cap per source table."
    ),
    labels: Path = typer.Option(
        None, "--labels", exists=True,
        help="Existing numeric-table labels to prefill and always include.",
    ),
) -> None:
    """Create a local HTML sheet for reviewing and labelling numeric table cells."""
    from pdf2md.table_review import create_table_review

    summary = create_table_review(
        version_dir,
        output_path=output,
        sample_size=sample,
        seed=seed,
        per_table=per_table,
        labels_path=labels,
    )
    typer.echo(
        f"prepared sample: {summary['sampled']}/{summary['available']} numeric cells, "
        f"{summary['prefilled']} prefilled\n{summary['html']}\n{summary['csv']}"
    )


@app.command()
def prune(
    keep: int = typer.Option(1, "--keep", "-k", min=0, help="Keep the newest N versions per document."),
    out: Path = typer.Option(None, "--out", "-o", help="Output root (default ./out)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be removed."),
) -> None:
    """Delete old output versions, keeping the newest N per document."""
    configure_cli_logging(verbose=False)
    if out:
        os.environ["PDF2MD_OUT"] = str(out)
    from pdf2md.cache import prune as prune_versions

    removed = prune_versions(keep=keep, dry_run=dry_run)
    verb = "would remove" if dry_run else "removed"
    for p in removed:
        typer.echo(f"{verb}  {p}")
    typer.echo(f"{verb} {len(removed)} version dir(s)")


@app.command()
def version() -> None:
    """Print pdf2md and engine versions."""
    from importlib.metadata import version as v

    typer.echo(f"pdf2md {__version__} (docling {v('docling')})")


@app.command()
def doctor(
    config: Path = typer.Option(
        None, "--config", "-c", exists=True, dir_okay=False, help="TOML config to inspect."
    ),
    probe_vlm: bool = typer.Option(
        False, "--probe-vlm", help="Contact the configured vision endpoint and list models."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit the machine-readable report."
    ),
) -> None:
    """Check the installation and configured optional features."""
    from pdf2md.doctor import inspect_environment

    cfg = _load_config(config)
    report = inspect_environment(cfg, probe_vlm=probe_vlm)
    if json_output:
        typer.echo(json.dumps(report, indent=2))
    else:
        marks = {
            "ok": "ok",
            "optional": "optional",
            "skipped": "skipped",
            "warning": "warning",
            "error": "ERROR",
        }
        for check in report["checks"]:
            typer.echo(f"[{marks[check['status']]:8}] {check['name']}: {check['detail']}")
            if check["fix"] and check["status"] in {"error", "warning", "optional"}:
                typer.echo(f"           fix: {check['fix']}")
        typer.echo(
            f"\n{'ready' if report['ready'] else 'not ready'} for "
            f"the configured {report['engine']} workflow"
        )
    if not report["ready"]:
        raise typer.Exit(1)


@models_app.command("pull")
def models_pull(
    local_dir: Path = typer.Option(
        None, "--local-dir", help="Download a model snapshot here for offline/reproducible use."
    ),
) -> None:
    """Download/warm the conversion models."""
    configure_cli_logging(verbose=True)
    from pdf2md.models import pull

    pull(local_dir)


@line_reader_app.command("prepare")
def line_reader_prepare(
    version_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    output_dir: Path = typer.Argument(...),
    tesseract_executable: str = typer.Option(
        "tesseract", "--tesseract-executable",
        help="Tesseract executable used only to locate source rows.",
    ),
    block_id: str = typer.Option(
        None, "--block-id", help="Prepare one source table block instead of every table.",
    ),
    page_from: int = typer.Option(
        None, "--page-from", min=1, help="Prepare tables from this source page onward.",
    ),
    page_to: int = typer.Option(
        None, "--page-to", min=1, help="Prepare tables through this source page.",
    ),
) -> None:
    """Prepare hash-pinned row-key crops for an external PP-OCRv6 run."""
    from pdf2md.line_reader import prepare

    configure_cli_logging(verbose=False)
    manifest = prepare(
        version_dir,
        output_dir,
        tesseract_executable,
        {block_id} if block_id else None,
        page_from=page_from,
        page_to=page_to,
    )
    typer.echo(
        f"line reader: {len(manifest['records'])}/{manifest['expected_key_cells']} "
        f"key crops prepared, {manifest['unprepared_key_cells']} unavailable across "
        f"{manifest['preparation_refusal_events']} refusal events\n"
        f"{output_dir / 'inputs.json'}"
    )


@line_reader_app.command("apply")
def line_reader_apply(
    output_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    run: Path = typer.Option(..., "--run", exists=True, dir_okay=False),
) -> None:
    """Validate a pinned PP-OCRv6 run and write a non-mutating evidence sidecar."""
    from pdf2md.line_reader import apply

    report = apply(output_dir, run)
    typer.echo(
        f"line reader: {report['reader_agreement']}/{report['prepared']} agreed, "
        f"{report['reader_refused']} reader-refused, "
        f"{report['unprepared_key_cells']} key cells unavailable across "
        f"{report['preparation_refusal_events']} preparation-refusal events\n"
        f"{output_dir / 'evidence.jsonl'}"
    )


def _report(results: list[ConvertResult]) -> None:
    for r in results:
        if r.failed:
            typer.echo(f"FAILED  {r.error or 'conversion failed'}")
            typer.echo(f"  next: {_failure_hint(r.error)}")
            continue
        profile = _read_profile(r.out_dir)
        c = r.coverage
        accounted_for = c.accounted_for if c else profile.get("accounted_for")
        review_counts = profile.get("review_counts") or {
            "action_required": sum(
                flag.disposition == "action_required" for flag in c.flags
            ) if c else profile.get("review_flags", 0)
        }
        needs_review = bool(review_counts.get("action_required"))
        optional_failures = failed_optional_calls(r.run_metrics)
        status = (
            "INCOMPLETE ACCOUNTING" if not accounted_for else
            "PARTIAL ENRICHMENT" if optional_failures else
            "REVIEW" if needs_review else
            "complete"
        )
        prefix = "cached" if r.cached else "ok"
        typer.echo(
            f"{prefix:6}  v{r.version}  [{status}]\n"
            f"  output: {r.out_dir}\n"
            f"  content: {_content_path(r, profile)}\n"
            f"  pages: {profile.get('pages', r.page_count)} | "
            f"markdown: {len(r.md_files)} | tables: {profile.get('tables', 0)} | "
            f"figures: {profile.get('figures', 0)} | equations: {profile.get('equations', 0)}\n"
            f"  action required: {review_counts.get('action_required', 0)} | "
            f"source-dependent: {review_counts.get('source_dependent', 0)}"
        )
        if r.run_metrics:
            stage_count = len(r.run_metrics.get("stages", {}))
            duration = _duration(float(r.run_metrics.get("duration_s", 0)))
            if r.cached:
                typer.echo(
                    f"  cache: reused {stage_count} completed stage(s); "
                    f"original run took {duration}"
                )
            else:
                typer.echo(f"  work: {stage_count} stage(s) in {duration}")
        if sum(review_counts.values()):
            typer.echo(f"  review details: {r.out_dir / 'review.md'}")
        if optional_failures:
            typer.echo(
                f"  optional model failures: {optional_failures}; completed regions are cached"
            )
            typer.echo(
                "  next: run `pdf2md doctor --probe-vlm`, then rerun the same command"
            )
        elif profile.get("tables_candidates", 0):
            typer.echo(f"  next: pdf2md review-tables {r.out_dir}")
        elif review_counts.get("action_required", 0):
            typer.echo(f"  next: review {r.out_dir / 'review.md'}")
        else:
            typer.echo(f"  next: read {_content_path(r, profile)}")
    if len(results) > 1:
        failed = sum(result.failed for result in results)
        cached = sum(result.cached and not result.failed for result in results)
        converted = len(results) - cached - failed
        typer.echo(
            f"summary: {len(results)} PDFs | {cached} cached | "
            f"{converted} converted | {failed} failed"
        )


def _read_profile(version_dir: Path) -> dict:
    try:
        return json.loads((version_dir / "profile.json").read_text())
    except (OSError, ValueError):
        return {}


def _read_document_fields(version_dir: Path) -> dict:
    try:
        document = json.loads((version_dir / "metadata.json").read_text()).get("document") or {}
    except (OSError, ValueError):
        return {}
    fields = document.get("fields") or {}
    return {
        name: (fields.get(name) or {}).get("value")
        for name in ("title", "authors", "year", "doi")
    }


def _content_path(result: ConvertResult, profile: dict) -> Path:
    contents = profile.get("contents")
    if contents:
        return result.out_dir / contents
    return result.md_files[0] if result.md_files else result.out_dir


def _failure_hint(message: str | None) -> str:
    lowered = (message or "").lower()
    if "vision" in lowered or "openai" in lowered or "connection" in lowered:
        return "run `pdf2md doctor --probe-vlm`, then retry with --verbose"
    if any(name in lowered for name in ("mineru", "tesseract", "surya", "pdftocairo")):
        return "run `pdf2md doctor`, correct the reported dependency, then retry"
    return "run `pdf2md doctor`, then retry with --verbose"


def main() -> None:
    app()


if __name__ == "__main__":
    main()
