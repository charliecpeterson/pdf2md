"""The describe seam is testable without a model or server: get_describer gates on
config, and prompt/data-URI building are pure functions. The OpenAI call itself is a
thin, lazy-imported adapter, exercised only when the pass is on against a real
endpoint."""

from __future__ import annotations

import base64

from pdf2md.config import Config
from pdf2md.describe import _data_uri, _prompt, get_describer


def test_describer_off_by_default():
    assert get_describer(Config()) is None


def test_prompt_is_kind_aware():
    assert "LaTeX" in _prompt("equation")
    assert "Markdown" in _prompt("table")
    assert "figure" in _prompt("figure").lower()
    assert _prompt("mystery") == _prompt("figure")  # unknown kind falls back to figure


def test_prompt_includes_context():
    p = _prompt("figure", "Figure 3.1: program flow")
    assert "Figure 3.1: program flow" in p
    assert _prompt("figure", "   ") == _prompt("figure")  # blank context dropped


def test_data_uri_is_base64_png(tmp_path):
    img = tmp_path / "crop.png"
    img.write_bytes(b"\x89PNG\r\nfake")
    uri = _data_uri(img)
    assert uri.startswith("data:image/png;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]) == b"\x89PNG\r\nfake"
