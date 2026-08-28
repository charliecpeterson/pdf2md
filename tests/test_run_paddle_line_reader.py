"""Model provenance excludes mutable downloader cache metadata."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPTS = Path(__file__).parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location(
    "run_paddle_line_reader", SCRIPTS / "run_paddle_line_reader.py"
)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


def test_model_hash_covers_runtime_files_but_not_download_metadata(tmp_path):
    for name, content in (
        ("inference.json", b"graph"),
        ("inference.pdiparams", b"weights"),
        ("inference.yml", b"config"),
    ):
        (tmp_path / name).write_bytes(content)
    metadata = tmp_path / ".cache" / "download.metadata"
    metadata.parent.mkdir()
    metadata.write_text("first timestamp")

    initial = runner._model_hash(tmp_path)
    metadata.write_text("second timestamp")

    assert runner._model_hash(tmp_path) == initial
    (tmp_path / "inference.pdiparams").write_bytes(b"different weights")
    assert runner._model_hash(tmp_path) != initial
