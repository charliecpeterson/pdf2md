"""Opt-in integration test: runs real Docling end-to-end. Skipped unless
`PDF2MD_TEST_PDF` points at a real PDF and `-m integration` is selected.

This is the engine-validation harness: point it at a representative document and
assert every engine-detected block receives a recorded disposition.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from pdf2md.config import Config
from pdf2md.pipeline import convert_file


@pytest.mark.integration
def test_real_convert_accounts_for_every_block(tmp_path, monkeypatch):
    pdf = os.environ.get("PDF2MD_TEST_PDF")
    if not pdf:
        pytest.skip("set PDF2MD_TEST_PDF to a real PDF to run this")
    monkeypatch.setenv("PDF2MD_OUT", str(tmp_path))

    result = convert_file(pdf, config=Config(do_formula_enrichment=False))

    assert not result.failed
    assert result.md_files
    assert result.coverage is not None and result.coverage.accounted_for


@pytest.mark.integration
def test_built_wheel_installs_in_a_clean_environment(tmp_path):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required for the clean-wheel integration test")
    root = Path(__file__).parent.parent
    wheel_dir = tmp_path / "wheel"
    environment = tmp_path / "venv"
    uv_env = os.environ | {"UV_CACHE_DIR": str(tmp_path / "uv-cache")}
    for variable in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        uv_env.pop(variable, None)
    subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(wheel_dir)],
        cwd=root,
        env=uv_env,
        check=True,
    )
    wheel = next(wheel_dir.glob("pdf2md-*.whl"))
    subprocess.run(
        [uv, "venv", "--python", sys.executable, str(environment)],
        env=uv_env,
        check=True,
    )
    python = environment / "bin" / "python"
    subprocess.run(
        [uv, "pip", "install", "--python", str(python), str(wheel)],
        env=uv_env,
        check=True,
    )
    executable = environment / "bin" / "pdf2md"
    help_run = subprocess.run(
        [executable, "--help"], cwd=tmp_path, env=uv_env,
        text=True, capture_output=True, check=True
    )
    doctor_run = subprocess.run(
        [executable, "doctor", "--json"], cwd=tmp_path, env=uv_env,
        text=True, capture_output=True, check=True
    )
    import_run = subprocess.run(
        [
            python,
            "-c",
            (
                "import pdf2md; "
                "from pdf2md.passages import load_passage_schema; "
                "assert load_passage_schema()['properties']['schema_version']['const'] == 2; "
                "print(pdf2md.__file__)"
            ),
        ],
        cwd=tmp_path,
        env=uv_env,
        text=True,
        capture_output=True,
        check=True,
    )
    report = json.loads(doctor_run.stdout)
    imported_package = Path(import_run.stdout.strip()).resolve()

    assert "Auditable PDF to markdown converter" in help_run.stdout
    assert report["ready"] is True
    assert report["engine"] == "docling"
    assert imported_package.is_relative_to(environment.resolve())
