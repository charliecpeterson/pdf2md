"""Typer CLI. Thin over the library; the only place a logging handler is installed."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import typer

from pdf2md import __version__
from pdf2md.cache import content_hash, doc_dir, latest_version
from pdf2md.config import Config
from pdf2md.logging import configure_cli_logging
from pdf2md.pipeline import ConvertResult, convert_dir, convert_file

app = typer.Typer(help="Lossless PDF to markdown converter.", no_args_is_help=True)
models_app = typer.Typer(help="Manage conversion models.")
app.add_typer(models_app, name="models")


@app.command()
def convert(
    path: Path = typer.Argument(..., exists=True, help="A PDF file or a directory of PDFs."),
    out: Path = typer.Option(None, "--out", "-o", help="Output root (default ./out)."),
    config: Path = typer.Option(None, "--config", "-c", exists=True, help="TOML config."),
    force: bool = typer.Option(False, "--force", "-f", help="Re-convert even if cached."),
    no_formula: bool = typer.Option(
        False, "--no-formula", help="Skip formula→LaTeX enrichment (much faster; for books/scans)."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Convert a PDF (or every PDF in a directory) to markdown."""
    configure_cli_logging(verbose)
    if out:
        os.environ["PDF2MD_OUT"] = str(out)
    cfg = Config.load(config) if config else Config()
    if no_formula:
        cfg = replace(cfg, do_formula_enrichment=False)

    if path.is_dir():
        results = convert_dir(path, config=cfg, force=force)
    else:
        results = [convert_file(path, config=cfg, force=force)]

    _report(results)
    if any(r.failed for r in results):
        raise typer.Exit(1)


@app.command()
def coverage(
    path: Path = typer.Argument(..., exists=True, help="A previously converted PDF."),
) -> None:
    """Print the coverage report for an already-converted PDF (no re-run)."""
    dd = doc_dir(content_hash(path))
    version = latest_version(dd)
    if version is None:
        typer.echo(f"not converted yet: {path}")
        raise typer.Exit(1)
    prov = json.loads((dd / f"v{version}" / "provenance.json").read_text())
    cov = prov.get("coverage") or {}
    typer.echo(json.dumps(cov, indent=2))


@app.command()
def version() -> None:
    """Print pdf2md and engine versions."""
    from importlib.metadata import version as v

    typer.echo(f"pdf2md {__version__} (docling {v('docling')})")


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


def _report(results: list[ConvertResult]) -> None:
    for r in results:
        if r.failed:
            typer.echo(f"FAILED  {r.error}")
            continue
        if r.cached:
            typer.echo(f"cached  v{r.version}  {r.out_dir}")
            continue
        c = r.coverage
        status = "lossless" if (c and c.lossless) else "INCOMPLETE"
        flagged = (c.flagged + c.dropped) if c else 0
        typer.echo(
            f"ok      v{r.version}  {len(r.md_files)} md, "
            f"{c.cropped if c else 0} crops, {flagged} flagged  [{status}]  {r.out_dir}"
        )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
