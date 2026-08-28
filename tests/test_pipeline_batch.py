"""Batch conversion keeps setup failures inside the per-document result contract."""

import hashlib

from pdf2md.config import Config
from pdf2md import pipeline


def test_batch_setup_failure_returns_one_failed_result_per_pdf(tmp_path, monkeypatch):
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    def fail_setup(engine, config):
        raise RuntimeError("missing Python.h")

    monkeypatch.setattr(pipeline, "_get_engine", fail_setup)

    results = pipeline.convert_dir(tmp_path, config=Config())

    assert [item.doc_id for item in results] == ["first.pdf", "second.pdf"]
    assert all(item.failed for item in results)
    assert [item.error for item in results] == ["missing Python.h", "missing Python.h"]


def test_batch_excludes_generated_sources_below_output_root(tmp_path, monkeypatch):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"paper")
    output = tmp_path / "out"
    stored_source = output / "paper-abc12345" / "source.pdf"
    stored_source.parent.mkdir(parents=True)
    stored_source.write_bytes(b"paper")
    converted = []

    monkeypatch.setenv("PDF2MD_OUT", str(output))
    monkeypatch.setattr(pipeline, "_get_engine", lambda engine, config: object())
    monkeypatch.setattr(pipeline, "get_transcriber", lambda config: None)
    monkeypatch.setattr(pipeline, "get_describer", lambda config: None)

    def record(pdf, **kwargs):
        converted.append(pdf)
        return pipeline.ConvertResult(pdf.name, 1, tmp_path, [])

    monkeypatch.setattr(pipeline, "convert_file", record)

    pipeline.convert_dir(tmp_path, config=Config())

    assert converted == [source]


def test_batch_keeps_original_pdfs_when_input_is_output_root(tmp_path, monkeypatch):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"paper")
    stored_source = (
        tmp_path / f"paper-{hashlib.sha256(b'paper').hexdigest()[:8]}" / "source.pdf"
    )
    stored_source.parent.mkdir()
    stored_source.write_bytes(b"paper")
    converted = []

    monkeypatch.setenv("PDF2MD_OUT", str(tmp_path))
    monkeypatch.setattr(pipeline, "_get_engine", lambda engine, config: object())
    monkeypatch.setattr(pipeline, "get_transcriber", lambda config: None)
    monkeypatch.setattr(pipeline, "get_describer", lambda config: None)

    def record(pdf, **kwargs):
        converted.append(pdf)
        return pipeline.ConvertResult(pdf.name, 1, tmp_path, [])

    monkeypatch.setattr(pipeline, "convert_file", record)

    pipeline.convert_dir(tmp_path, config=Config())

    assert converted == [source]
