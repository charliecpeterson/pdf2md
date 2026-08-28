"""The deterministic table diagnostic distinguishes partial and missing reads."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "eval_pdf_parse_bench",
    Path(__file__).parent.parent / "scripts" / "eval_pdf_parse_bench.py",
)
ent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ent)


def test_pdf_parse_bench_numeric_diagnostic(tmp_path):
    output = tmp_path / "output" / "doc" / "v1"
    table_dir = output / "data" / "tables"
    table_dir.mkdir(parents=True)
    (table_dir / "table.json").write_text(json.dumps({
        "rows": [["Method", "Value"], ["A", "1.20"]],
    }))
    (output / "manifest.json").write_text(json.dumps({
        "representations": {"tables": [{
            "block_id": "#/table", "json": "data/tables/table.json",
        }]},
    }))
    (output / "provenance.json").write_text(json.dumps({"source_path": "/input/001.pdf"}))
    ground = tmp_path / "ground"
    ground.mkdir()
    (ground / "001.json").write_text(json.dumps([
        {"type": "table", "data": "\\begin{tabular}{lc} A & 1.20 \\\\ \\end{tabular}"},
        {"type": "table", "data": "\\begin{tabular}{lc} B & 9.0 \\\\ \\end{tabular}"},
    ]))

    report = ent.evaluate(tmp_path / "output", ground)

    assert report["summary"] == {
        "documents": 1,
        "ground_tables": 2,
        "extracted_tables": 1,
        "numeric_tables": 2,
        "agree": 1,
        "disagree": 0,
        "tool_refused": 1,
        "numeric_recall": 0.5,
    }


def test_pdf_parse_bench_reads_ocrflux_html_tables(tmp_path):
    markdown = tmp_path / "markdowns" / "001"
    markdown.mkdir(parents=True)
    (markdown / "001.md").write_text(
        "<table><tr><th>Method</th><th>Value</th></tr>"
        "<tr><td>A</td><td>1.20</td></tr></table>"
    )
    ground = tmp_path / "ground"
    ground.mkdir()
    (ground / "001.json").write_text(json.dumps([
        {"type": "table", "data": "\\begin{tabular}{lc} A & 1.20 \\\\ \\end{tabular}"},
    ]))

    report = ent.evaluate_ocrflux(tmp_path / "markdowns", ground)

    assert report["summary"] == {
        "documents": 1,
        "ground_tables": 1,
        "extracted_tables": 1,
        "numeric_tables": 1,
        "agree": 1,
        "disagree": 0,
        "tool_refused": 0,
        "numeric_recall": 1.0,
    }
    assert report["documents"][0]["records"][0]["block_id"] == "ocrflux/table/0"
