"""The commands that read a bundle rather than produce one.

`convert` and `enrich` write; everything here inspects, compares or reports —
coverage, run diffs, search, the table review sheets, the environment doctor.
Splitting on that line keeps the two commands that can change output in one file
where their flags can be read together.

Registration happens on import: `cli` imports this module after building `app`,
so the decorators here attach to the same Typer instance.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from pdf2md import __version__
from pdf2md.cache import content_hash, doc_dir, document_dirs, latest_version, out_root
from pdf2md.cli import (
    _load_config,
    _replace_config,
    app,
    line_reader_app,
    models_app,
)
from pdf2md.cli_report import _read_document_fields, _read_profile
from pdf2md.logging import _duration, configure_cli_logging
from pdf2md.search import find_passages


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
    engine: str = typer.Option(
        None, "--engine",
        help="Check readiness for this engine (docling, mineru, marker) without a config file.",
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
    if engine:
        cfg = _replace_config(cfg, engine=engine)
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
