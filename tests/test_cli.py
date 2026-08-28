"""CLI handoff output points users to the useful artifact and next action."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from typer.testing import CliRunner

from pdf2md.cache import content_hash, doc_dir
from pdf2md.cli import _failure_hint, _report, app
from pdf2md.pipeline import ConvertResult


def test_report_summarizes_cached_output_from_profile(tmp_path, capsys):
    version_dir = tmp_path / "document" / "v3"
    version_dir.mkdir(parents=True)
    markdown = version_dir / "document.md"
    markdown.write_text("# Paper\n")
    (version_dir / "profile.json").write_text(json.dumps({
        "pages": 12,
        "tables": 4,
        "figures": 2,
        "equations": 7,
        "tables_candidates": 2,
        "accounted_for": True,
        "needs_review": True,
        "review_flags": 3,
        "contents": "document.md",
    }))
    result = ConvertResult(
        "abc",
        3,
        version_dir,
        [markdown],
        page_count=12,
        cached=True,
        run_metrics={
            "duration_s": 65.0,
            "stages": {"parse": {}, "emit": {}},
        },
    )

    _report([result])

    output = capsys.readouterr().out
    assert "cached  v3  [REVIEW]" in output
    assert f"content: {markdown}" in output
    assert "pages: 12 | markdown: 1 | tables: 4 | figures: 2 | equations: 7" in output
    assert "cache: reused 2 completed stage(s); original run took 1m 05s" in output
    assert f"next: pdf2md review-tables {version_dir}" in output


def test_report_gives_actionable_failure_hint(tmp_path, capsys):
    result = ConvertResult(
        "abc",
        0,
        tmp_path,
        [],
        failed=True,
        error="MinerU executable not found: mineru",
    )

    _report([result])

    output = capsys.readouterr().out
    assert "FAILED  MinerU executable not found" in output
    assert "run `pdf2md doctor`" in output
    assert _failure_hint("vision endpoint connection refused").startswith(
        "run `pdf2md doctor --probe-vlm`"
    )


def test_compare_runs_reads_stored_stage_metrics(tmp_path):
    before = tmp_path / "v1"
    after = tmp_path / "v2"
    before.mkdir()
    after.mkdir()
    for version, duration, parse in ((before, 10.0, 8.0), (after, 7.0, 6.0)):
        (version / "provenance.json").write_text(json.dumps({
            "provenance": {
                "run_metrics": {
                    "duration_s": duration,
                    "stages": {"parse": {"duration_s": parse}},
                    "memory": {
                        "available": True,
                        "main_process_peak_rss_bytes": (
                            100 * 1024**2 if version == before else 80 * 1024**2
                        ),
                        "largest_terminated_child_peak_rss_bytes": 0,
                    },
                }
            }
        }))

    result = CliRunner().invoke(app, ["compare-runs", str(before), str(after)])

    assert result.exit_code == 0
    assert "total  10s -> 7s (-30.0%)" in result.stdout
    assert "memory 100.0 MiB -> 80.0 MiB (-20.0%)" in result.stdout
    assert "parse" in result.stdout and "-25.0%" in result.stdout


def test_report_gives_a_next_action_when_there_are_no_tables(tmp_path, capsys):
    version_dir = tmp_path / "document" / "v1"
    version_dir.mkdir(parents=True)
    markdown = version_dir / "document.md"
    markdown.write_text("# Paper\n")
    (version_dir / "profile.json").write_text(json.dumps({
        "pages": 1,
        "tables": 0,
        "figures": 0,
        "equations": 0,
        "accounted_for": True,
        "needs_review": False,
        "review_flags": 0,
        "contents": "document.md",
    }))

    _report([ConvertResult("abc", 1, version_dir, [markdown], page_count=1)])

    assert f"next: read {markdown}" in capsys.readouterr().out


def test_report_does_not_recommend_review_for_verified_tables(tmp_path, capsys):
    version_dir = tmp_path / "document" / "v1"
    version_dir.mkdir(parents=True)
    markdown = version_dir / "document.md"
    markdown.write_text("# Paper\n")
    (version_dir / "profile.json").write_text(json.dumps({
        "pages": 2,
        "tables": 1,
        "tables_candidates": 0,
        "figures": 0,
        "equations": 0,
        "accounted_for": True,
        "needs_review": False,
        "review_flags": 0,
        "contents": "document.md",
    }))

    _report([ConvertResult("abc", 1, version_dir, [markdown], page_count=2)])

    output = capsys.readouterr().out
    assert f"next: read {markdown}" in output
    assert "review-tables" not in output


def test_report_points_non_table_review_to_queue(tmp_path, capsys):
    version_dir = tmp_path / "document" / "v1"
    version_dir.mkdir(parents=True)
    markdown = version_dir / "document.md"
    markdown.write_text("# Paper\n")
    profile = version_dir / "profile.json"
    profile.write_text(json.dumps({
        "pages": 2,
        "tables": 0,
        "figures": 0,
        "equations": 1,
        "accounted_for": True,
        "needs_review": True,
        "review_flags": 1,
        "contents": "document.md",
    }))

    _report([ConvertResult("abc", 1, version_dir, [markdown], page_count=2)])

    assert f"next: review {version_dir / 'review.md'}" in capsys.readouterr().out


def test_report_marks_failed_optional_work_as_retryable(tmp_path, capsys):
    version_dir = tmp_path / "document" / "v2"
    version_dir.mkdir(parents=True)
    markdown = version_dir / "document.md"
    markdown.write_text("# Paper\n")
    (version_dir / "profile.json").write_text(json.dumps({
        "pages": 2,
        "tables": 0,
        "figures": 1,
        "equations": 0,
        "accounted_for": True,
        "needs_review": False,
        "review_flags": 0,
        "contents": "document.md",
    }))
    metrics = {
        "duration_s": 3.0,
        "stages": {
            "descriptions": {
                "duration_s": 3.0,
                "counts": {"vision_failures": 2},
            }
        },
    }

    _report([
        ConvertResult(
            "abc", 2, version_dir, [markdown], page_count=2, run_metrics=metrics
        )
    ])

    output = capsys.readouterr().out
    assert "ok      v2  [PARTIAL ENRICHMENT]" in output
    assert "optional model failures: 2; completed regions are cached" in output
    assert "run `pdf2md doctor --probe-vlm`, then rerun the same command" in output


def test_coverage_accepts_the_output_root_used_for_conversion(tmp_path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"source")
    output = tmp_path / "library"
    version_dir = doc_dir(content_hash(source), source, root=output) / "v1"
    version_dir.mkdir(parents=True)
    (version_dir.parent / "source.pdf").write_bytes(source.read_bytes())
    (version_dir / "provenance.json").write_text(json.dumps({
        "coverage": {
            "total_blocks": 3,
            "emitted": 2,
            "cropped": 1,
            "flagged": 0,
            "dropped": 0,
            "flags": [{"block_id": "#/eq/1", "reason": "image authoritative"}],
        }
    }))

    result = CliRunner().invoke(
        app, ["coverage", str(source), "--out", str(output)]
    )

    assert result.exit_code == 0
    assert f'"output": "{version_dir.resolve()}"' in result.stdout
    assert '"accounted_for": true' in result.stdout
    assert '"flagged_blocks": 0' in result.stdout
    assert '"flag_count": 1' in result.stdout
    assert '"required": true' in result.stdout


def test_list_summarizes_documents_recursively_without_original_sources(tmp_path):
    output = tmp_path / "library"
    source_bytes = b"paper"
    document = output / "team" / f"paper-{hashlib.sha256(source_bytes).hexdigest()[:8]}"
    version = document / "v2"
    version.mkdir(parents=True)
    (document / "source.pdf").write_bytes(source_bytes)
    (version / "document.md").write_text("# Paper\n")
    (version / "profile.json").write_text(json.dumps({
        "source": "Original Paper.pdf",
        "pages": 12,
        "accounted_for": True,
        "needs_review": True,
        "review_flags": 3,
        "contents": "document.md",
    }))
    (version / "metadata.json").write_text(json.dumps({
        "document": {
            "fields": {
                "title": {"value": "A Useful Paper"},
                "authors": {"value": ["A. Author", "B. Author"]},
                "year": {"value": "2026"},
                "doi": {"value": "10.1234/example"},
            }
        }
    }))
    (version / "provenance.json").write_text("{}")
    unrelated = output / "experiment" / "v1"
    unrelated.mkdir(parents=True)

    result = CliRunner().invoke(app, ["list", "--out", str(output)])

    assert result.exit_code == 0
    assert "A Useful Paper" in result.stdout
    assert "authors: A. Author; B. Author | year: 2026 | DOI: 10.1234/example" in result.stdout
    assert "Original Paper.pdf" in result.stdout
    assert "v2  [REVIEW]  12 pages  3 review markers" in result.stdout
    assert str(version / "document.md") in result.stdout
    assert "experiment" not in result.stdout


def test_report_summarizes_cached_converted_and_failed_batch(tmp_path, capsys):
    converted = ConvertResult("new", 1, tmp_path / "new", [])
    cached = ConvertResult("old", 2, tmp_path / "old", [], cached=True)
    failed = ConvertResult("bad", 0, tmp_path, [], failed=True, error="bad PDF")

    _report([converted, cached, failed])

    assert (
        "summary: 3 PDFs | 1 cached | 1 converted | 1 failed"
        in capsys.readouterr().out
    )


def test_review_tables_calls_the_generated_sheet_a_prepared_sample(monkeypatch, tmp_path):
    version_dir = tmp_path / "v1"
    version_dir.mkdir()
    monkeypatch.setattr(
        "pdf2md.table_review.create_table_review",
        lambda *args, **kwargs: {
            "sampled": 12,
            "available": 20,
            "prefilled": 0,
            "html": version_dir / "table-review.html",
            "csv": version_dir / "table-review.csv",
        },
    )

    result = CliRunner().invoke(app, ["review-tables", str(version_dir)])

    assert result.exit_code == 0
    assert "prepared sample: 12/20 numeric cells, 0 prefilled" in result.stdout
    assert "reviewed sample" not in result.stdout


def test_invalid_engine_is_a_cli_error_without_running_conversion(tmp_path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"source")

    result = CliRunner().invoke(app, ["convert", str(source), "--engine", "unknown"])

    assert result.exit_code == 2
    assert "engine must be 'docling' or 'mineru'" in result.output
    assert "Traceback" not in result.output


def test_convert_help_groups_advanced_options():
    result = CliRunner().invoke(app, ["convert", "--help"])

    assert result.exit_code == 0
    for panel in (
        "Input and output",
        "Scans and OCR",
        "Equations",
        "Figures and data",
        "Vision models",
        "Verification",
    ):
        assert panel in result.output


def test_enrich_prints_preflight_and_reports_new_version(tmp_path, monkeypatch):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"source")
    version = tmp_path / "paper-deadbeef" / "v1"
    output = version.parent / "v2"
    output.mkdir(parents=True)
    markdown = output / "document.md"
    markdown.write_text("# Paper\n")
    (output / "profile.json").write_text(json.dumps({
        "pages": 240,
        "tables": 0,
        "figures": 4,
        "equations": 12,
        "accounted_for": True,
        "needs_review": False,
        "review_flags": 0,
        "contents": "document.md",
    }))
    monkeypatch.setattr("pdf2md.enrichment.resolve_version", lambda *args, **kwargs: version)
    monkeypatch.setattr(
        "pdf2md.enrichment.preflight",
        lambda *args, **kwargs: SimpleNamespace(
            source_version=version,
            pages=240,
            stages=("descriptions", "equations"),
            equations=12,
            equation_regions=9,
            equation_transcriptions=2,
            figures=4,
            chart_datasets=1,
            chart_model_candidates=3,
            description_regions=16,
            descriptions_present=5,
        ),
    )
    monkeypatch.setattr("pdf2md.enrichment.config_from_version", lambda *args: object())
    monkeypatch.setattr(
        "pdf2md.enrichment.enrich_version",
        lambda *args, **kwargs: ConvertResult(
            "doc", 2, output, [markdown], page_count=240
        ),
    )

    result = CliRunner().invoke(
        app, ["enrich", str(source), "--equations", "--descriptions"]
    )

    assert result.exit_code == 0
    assert "preflight: v1, 240 pages; stages: descriptions, equations" in result.stdout
    assert "9 image-backed equations (2 already transcribed)" in result.stdout
    assert "16 eligible crop descriptions (5 already present)" in result.stdout
    assert "large document: model-backed stages may take hours" in result.stdout
    assert "ok      v2  [complete]" in result.stdout


def test_enrich_dry_run_does_not_start_enrichment(tmp_path, monkeypatch):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"source")
    version = tmp_path / "paper-deadbeef" / "v1"
    monkeypatch.setattr("pdf2md.enrichment.resolve_version", lambda *args, **kwargs: version)
    monkeypatch.setattr(
        "pdf2md.enrichment.preflight",
        lambda *args, **kwargs: SimpleNamespace(
            source_version=version,
            pages=20,
            stages=("charts",),
            equations=0,
            equation_regions=0,
            equation_transcriptions=0,
            figures=7,
            chart_datasets=3,
            chart_model_candidates=4,
            description_regions=0,
            descriptions_present=0,
        ),
    )
    monkeypatch.setattr("pdf2md.enrichment.config_from_version", lambda *args: object())
    monkeypatch.setattr(
        "pdf2md.enrichment.enrich_version",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    result = CliRunner().invoke(
        app, ["enrich", str(source), "--charts", "--dry-run"]
    )

    assert result.exit_code == 0
    assert "7 figures (3 already have data; up to 4 model candidates)" in result.stdout
    assert "dry run: no version or cache files written" in result.stdout


def test_enrich_requires_a_stage(tmp_path):
    version = tmp_path / "v1"
    version.mkdir()
    (version / "provenance.json").write_text("{}")

    result = CliRunner().invoke(app, ["enrich", str(version)])

    assert result.exit_code == 2
    assert "choose at least one enrichment stage" in result.output
