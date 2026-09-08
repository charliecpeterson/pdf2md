"""What the terminal sees when a conversion finishes.

The last line of a run is the only part of the audit most people read, so it has
to lead with the thing that decides whether the output can be used. That ordering
is deliberate: accounting first (did anything leave without a disposition), then
the worst outstanding item, then where to look. A summary that opened with
"12 files written" would be true and useless.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from pdf2md.logging import _duration
from pdf2md.run_metrics import failed_optional_calls
from pdf2md.schema import ConvertResult


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
            f"  action required: {review_counts.get('action_required', 0)}"
            f"{_worst_item(c)} | "
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

def _worst_item(coverage) -> str:
    """Name the worst review item inline, so the count is a reason to open the file.

    "action required: 7" followed by a path is a number a reader can skip past;
    "(high: p16 table structure)" is the thing they were about to go hunting for
    by hand.
    """
    flags = getattr(coverage, "flags", None) or []
    actionable = [f for f in flags if f.disposition == "action_required"]
    if not actionable:
        return ""
    rank = {"high": 0, "medium": 1, "low": 2}
    worst = min(actionable, key=lambda f: (rank.get(f.severity, 9),
                                           rank.get(f.content_impact, 9), f.page))
    reason = worst.reason.split("\u2014")[0].split(",")[0].strip()
    if len(reason) > 46:  # cut at a word boundary; a chopped identifier reads as a bug
        reason = reason[:46].rsplit(" ", 1)[0] + "..."
    return f" ({worst.severity}: p{worst.page} {reason})"

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
