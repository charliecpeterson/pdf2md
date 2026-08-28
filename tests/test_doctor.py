"""Environment diagnostics distinguish required failures from optional tools."""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

from typer.testing import CliRunner

from pdf2md.config import Config
from pdf2md import doctor
from pdf2md.cli import app


def test_default_diagnostics_do_not_require_optional_tools(monkeypatch):
    def installed(name):
        if name in {"docling", "pypdfium2", "rapidocr", "onnxruntime"}:
            return "1.0"
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(
        "pdf2md.engines.docling.missing_cuda_python_headers", lambda device: None
    )
    monkeypatch.setattr(doctor.metadata, "version", installed)
    monkeypatch.setattr(doctor.shutil, "which", lambda command: None)

    report = doctor.inspect_environment(Config())

    assert report["ready"] is True
    statuses = {check["name"]: check["status"] for check in report["checks"]}
    assert statuses["docling"] == "ok"
    assert statuses["CUDA formula headers"] == "ok"
    assert statuses["MinerU"] == "optional"
    assert statuses["Tesseract"] == "optional"
    assert statuses["vision endpoint"] == "skipped"


def test_active_features_make_missing_dependencies_errors(monkeypatch):
    def installed(name):
        if name in {"docling", "pypdfium2", "rapidocr", "onnxruntime"}:
            return "1.0"
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(doctor.metadata, "version", installed)
    monkeypatch.setattr(doctor.shutil, "which", lambda command: None)

    report = doctor.inspect_environment(Config(
        engine="mineru",
        transcribe_equations=True,
        describe_figures=True,
        figure_svg=True,
        table_ocr_executable="missing-tesseract",
    ))

    assert report["ready"] is False
    errors = {
        check["name"] for check in report["checks"] if check["status"] == "error"
    }
    assert errors == {"MinerU", "Tesseract", "pdftocairo", "surya-ocr", "openai"}


def test_missing_default_ocr_dependencies_make_doctor_not_ready(monkeypatch):
    def installed(name):
        if name in {"docling", "pypdfium2"}:
            return "1.0"
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(
        "pdf2md.engines.docling.missing_cuda_python_headers", lambda device: None
    )
    monkeypatch.setattr(doctor.metadata, "version", installed)
    monkeypatch.setattr(doctor.shutil, "which", lambda command: None)

    report = doctor.inspect_environment(Config())

    assert report["ready"] is False
    errors = {
        check["name"] for check in report["checks"] if check["status"] == "error"
    }
    assert errors == {"rapidocr", "onnxruntime"}


def test_active_vlm_requires_the_configured_model(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"data": []}'

    monkeypatch.setattr(doctor.request, "urlopen", lambda *args, **kwargs: Response())

    check = doctor._probe_vlm(Config(describe_figures=True), 1.0)

    assert check.status == "error"
    assert "configured model(s) absent" in check.detail


def test_vlm_probe_rejects_a_non_object_response(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b"[]"

    monkeypatch.setattr(doctor.request, "urlopen", lambda *args, **kwargs: Response())

    check = doctor._probe_vlm(Config(describe_figures=True), 1.0)

    assert check.status == "error"
    assert "invalid response" in check.detail


def test_configured_paths_are_checked(tmp_path):
    reference = tmp_path / "reference.csv"
    reference.write_text("atomic_number,row_key,column,value\n")

    assert doctor._configured_file(
        "table_reference_path", str(reference), kind="reference CSV"
    ).status == "ok"
    missing = doctor._configured_file(
        "local_model_dir", str(tmp_path / "missing"), kind="model directory"
    )
    assert missing.status == "error"
    assert "does not exist" in missing.detail


def test_cuda_formula_headers_are_required_when_the_gpu_path_needs_them(monkeypatch):
    missing = Path("/usr/include/python3.11/Python.h")
    monkeypatch.setattr(
        "pdf2md.engines.docling.missing_cuda_python_headers", lambda device: missing
    )

    check = doctor._formula_headers(Config())

    assert check.status == "error"
    assert str(missing) in check.detail
    assert "--no-formula" in check.fix


def test_doctor_cli_prints_fixes_and_fails_when_not_ready(monkeypatch):
    monkeypatch.setattr(doctor, "inspect_environment", lambda config, probe_vlm: {
        "schema_version": 1,
        "ready": False,
        "engine": "mineru",
        "active_optional_features": {},
        "checks": [{
            "name": "MinerU",
            "status": "error",
            "detail": "not found on PATH: mineru",
            "fix": "Set mineru_executable.",
        }],
    })

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "[ERROR" in result.stdout
    assert "fix: Set mineru_executable." in result.stdout
    assert "not ready for the configured mineru workflow" in result.stdout
