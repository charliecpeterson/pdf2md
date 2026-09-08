"""Typer CLI. Thin over the library; the only place a logging handler is installed."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import typer

from pdf2md.cli_report import _report
from pdf2md.config import Config
from pdf2md.logging import configure_cli_logging
from pdf2md.pipeline import convert_dir, convert_file

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
        None, "--engine",
        help="Parser backend: docling (default), mineru, marker, or auto "
             "(mineru for a scan where it is installed, docling otherwise).",
        rich_help_panel="Input and output",
    ),
    marker_executable: str = typer.Option(
        None, "--marker-executable",
        help="Path to the Marker CLI when --engine marker uses a separate environment.",
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
    tables_only: bool = typer.Option(
        False, "--tables-only",
        help="Hunting one table: skip formula enrichment, chart digitization and figure "
             "OCR. Tables, their audits and their crops are unaffected. Measured at "
             "27% off a 28-page scan (1m40s to 1m13s) — the parse dominates, so this "
             "trims rather than transforms.",
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
    if marker_executable:
        cfg = _replace_config(cfg, marker_executable=marker_executable)
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
    if tables_only:
        # Everything a table reader does not need. Table crops, cells and audits are
        # untouched, so the evidence tables depend on is all still there.
        #
        # Measured rather than assumed, and the assumption was wrong: on a 28-page
        # scan this saves 27% (1m40s -> 1m13s) and every second of it is formula
        # enrichment, which runs inside Docling's parse. Chart digitization and
        # figure OCR do not reach the top five stages. The parse is 94 of 100
        # seconds, and finding a table requires it, so no flag can avoid it.
        cfg = _replace_config(
            cfg,
            do_formula_enrichment=False,
            digitize_figures=False,
            digitize_vlm=False,
            ocr_figures=False,
            figure_svg=False,
        )

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




































def main() -> None:
    app()


if __name__ == "__main__":
    main()


# Registers the read-only commands on `app`. Imported last: the decorators there
# need `app` to exist, and nothing in this module needs them.
from pdf2md import cli_inspect  # noqa: E402,F401
