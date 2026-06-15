"""Opt-in integration test: runs real Docling end-to-end. Skipped unless
`PDF2MD_TEST_PDF` points at a real PDF and `-m integration` is selected.

This is the engine-validation harness: point it at a representative document and
assert the conversion stays lossless.
"""

from __future__ import annotations

import os

import pytest

from pdf2md.config import Config
from pdf2md.pipeline import convert_file


@pytest.mark.integration
def test_real_convert_is_lossless(tmp_path):
    pdf = os.environ.get("PDF2MD_TEST_PDF")
    if not pdf:
        pytest.skip("set PDF2MD_TEST_PDF to a real PDF to run this")
    os.environ["PDF2MD_OUT"] = str(tmp_path)

    result = convert_file(pdf, config=Config(do_formula_enrichment=False))

    assert not result.failed
    assert result.md_files
    assert result.coverage is not None and result.coverage.lossless
