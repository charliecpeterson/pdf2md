"""The bake-off runner must compare exact sources and retain native evidence."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "engine_bakeoff", Path(__file__).parent.parent / "scripts" / "engine_bakeoff.py"
)
eb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eb)


def test_docling_cli_generation_is_explicit():
    legacy = "Usage: docling [OPTIONS] source\n"
    current = "Usage: docling [OPTIONS] COMMAND [ARGS]...\nCommands:\n  convert  Convert files\n"

    assert not eb._docling_uses_convert(legacy)
    assert eb._docling_uses_convert(current)

    old_command, old_details = eb._build_command(
        "docling-standard",
        "/tools/docling",
        Path("paper.pdf"),
        Path("native"),
        docling_help=legacy,
    )
    new_command, new_details = eb._build_command(
        "docling-standard",
        "/tools/docling",
        Path("paper.pdf"),
        Path("native"),
        docling_help=current,
    )
    assert old_command[:2] == ["/tools/docling", "--pipeline"]
    assert new_command[:3] == ["/tools/docling", "convert", "--pipeline"]
    assert old_details["cli_generation"] == "single-command"
    assert new_details["cli_generation"] == "convert-subcommand"


def test_each_candidate_has_a_pinned_native_command():
    source = Path("paper.pdf")
    native = Path("native")
    legacy_help = "Usage: docling [OPTIONS] source\n"

    current, _ = eb._build_command("pdf2md-current", "pdf2md", source, native)
    page_vlm, _ = eb._build_command("pdf2md-page-vlm", "pdf2md", source, native)
    standard, _ = eb._build_command(
        "docling-standard", "docling", source, native, docling_help=legacy_help
    )
    vlm, _ = eb._build_command(
        "docling-vlm", "docling", source, native, docling_help=legacy_help
    )
    paddle, _ = eb._build_command("paddleocr-vl", "paddleocr", source, native)
    mineru, _ = eb._build_command("mineru", "mineru", source, native)

    assert current == [
        "pdf2md", "convert", "paper.pdf", "--out", "native", "--force", "--figure-svg"
    ]
    assert page_vlm == [
        "pdf2md", "convert", "paper.pdf", "--out", "native", "--force", "--figure-svg",
        "--ocr-page-vlm", "--vlm-ocr-model", "glm-ocr:q8_0",
    ]
    assert "--enrich-chart-extraction" in standard
    assert vlm[vlm.index("--vlm-model") + 1] == "granite_docling"
    assert paddle == [
        "paddleocr", "doc_parser", "-i", "paper.pdf", "--device", "cpu",
        "--save_path", "native",
    ]
    assert mineru == [
        "mineru", "-p", "paper.pdf", "-o", "native", "-b", "hybrid-engine",
        "--effort", "high", "--image-analysis", "true",
    ]


def test_docling_layout_candidate_command_retains_immutable_model_pin():
    command, details = eb._build_command(
        "docling-egret-large",
        "/project/.venv/bin/python",
        Path("paper.pdf"),
        Path("native"),
    )

    assert command == [
        "/project/.venv/bin/python",
        str(eb._LAYOUT_RUNNER),
        "--candidate", "docling-egret-large",
        "--candidates", str(eb._LAYOUT_CANDIDATES),
        "--output", "native",
        "paper.pdf",
    ]
    assert details["layout_candidate"]["revision"] == (
        "fff417c78abd6bab338c87706c95a8d79dc68f1e"
    )
    assert details["layout_candidate"]["weight_sha256"] == (
        "f79def9d4a0d4e6e62cab25ec7846d1579ef1ef657c39554363813f7d1a14f1b"
    )


def test_manifest_rejects_changed_source(tmp_path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"changed")
    document = {"id": "paper", "path": "paper.pdf", "sha256": "wrong"}

    with pytest.raises(ValueError, match="sha256"):
        eb._source(document, tmp_path)


def test_sampled_page_records_parent_and_effective_hash(tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"parent pdf")
    fake = tmp_path / "pdfseparate"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, sys\n"
        "page = sys.argv[sys.argv.index('-f') + 1]\n"
        "source = pathlib.Path(sys.argv[-2])\n"
        "target = pathlib.Path(sys.argv[-1].replace('%d', page))\n"
        "target.write_bytes(source.read_bytes() + b' page ' + page.encode())\n"
    )
    fake.chmod(0o755)
    document = {"id": "sample", "sha256": eb._sha256(source), "page": 26}
    input_root = tmp_path / "inputs"

    selected, record = eb._materialize_input(document, source, input_root, str(fake))
    selected_again, reused = eb._materialize_input(document, source, input_root, str(fake))

    assert selected.read_bytes() == b"parent pdf page 26"
    assert record["selection"] == {"source_page": 26}
    assert record["sha256"] == eb._sha256(selected)
    assert record["producer"]["reused"] is False
    assert selected_again == selected
    assert reused["producer"]["reused"] is True
    assert reused["sha256"] == record["sha256"]
    assert document["sha256"] == eb._sha256(source)


def test_native_run_records_command_logs_and_output_hashes(tmp_path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"pdf bytes")
    fake = tmp_path / "pdf2md"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, sys\n"
        "if sys.argv[1:] == ['version']:\n"
        "    print('fake 1.2.3')\n"
        "    raise SystemExit\n"
        "out = pathlib.Path(sys.argv[sys.argv.index('--out') + 1])\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'document.md').write_text('# exact output\\n')\n"
        "print('native stdout')\n"
    )
    fake.chmod(0o755)
    document = {
        "id": "paper",
        "path": "paper.pdf",
        "sha256": eb._sha256(source),
        "archetypes": ["test"],
    }

    run_dir, record = eb._run_one(
        "pdf2md-current", document, source, tmp_path / "runs", str(fake), None
    )

    assert record["status"] == "ok"
    assert record["exit_code"] == 0
    assert record["source"]["sha256"] == eb._sha256(source)
    assert record["engine"]["version_probe"]["stdout"] == "fake 1.2.3\n"
    assert (run_dir / "stdout.log").read_text() == "native stdout\n"
    assert (run_dir / "stderr.log").read_text() == ""
    assert record["outputs"] == [{
        "path": "native/document.md",
        "bytes": 15,
        "sha256": eb._sha256(run_dir / "native" / "document.md"),
    }]
    assert json.loads((run_dir / "run.json").read_text())["command"] == record["command"]
    if os.name == "posix":
        assert record["resources"]["peak_rss_bytes"] > 0
    else:
        assert record["resources"] == {}


def test_unavailable_engine_is_a_recorded_failure(tmp_path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"pdf")
    document = {"id": "paper", "sha256": eb._sha256(source)}

    run_dir, record = eb._run_one(
        "mineru", document, source, tmp_path / "runs", None, None
    )

    assert record["status"] == "unavailable"
    assert record["error"] == "executable not found"
    assert record["outputs"] == []
    assert (run_dir / "stdout.log").read_text() == ""
    assert (run_dir / "stderr.log").read_text() == ""
    assert (run_dir / "run.json").is_file()
