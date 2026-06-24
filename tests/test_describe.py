"""The describe seam is testable without a model or server: get_describer gates on
config, and prompt/data-URI building are pure functions. The OpenAI call itself is a
thin, lazy-imported adapter, exercised only when the pass is on against a real
endpoint."""

from __future__ import annotations

import base64
from dataclasses import replace

import pytest

from pdf2md.config import Config
from pdf2md.describe import _data_uri, _prompt, get_describer


def test_describer_off_by_default():
    assert get_describer(Config()) is None


def test_model_routing_by_kind():
    pytest.importorskip("openai")
    d = get_describer(replace(Config(), describe_figures=True, vlm_model="vlm", vlm_ocr_model="ocr"))
    assert d.model_for("figure") == "vlm"        # plots -> general VLM
    assert d.model_for("table") == "ocr"         # dense grid -> OCR model
    assert d.model_for("ocr") == "ocr"           # scanned-page text -> OCR model
    assert d.model_for("equation") == "vlm"      # equations transcribe cleaner on the VLM
    d2 = get_describer(replace(Config(), describe_figures=True, vlm_model="vlm"))
    assert d2.model_for("table") == "vlm"        # no ocr_model -> everything on the main model


def test_describer_built_for_ocr_vlm_only():
    pytest.importorskip("openai")
    # --ocr-vlm alone (no --describe) must still build a describer.
    assert get_describer(replace(Config(), ocr_vlm=True)) is not None
    assert get_describer(Config()) is None  # both off -> none


def test_describe_counts_failures(tmp_path, monkeypatch):
    # A dropped endpoint connection must be counted, not silently swallowed, so a
    # degraded whole-document run can be surfaced instead of passing as clean.
    pytest.importorskip("openai")
    from pdf2md.describe import OpenAIVisionDescriber

    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n")
    d = OpenAIVisionDescriber(base_url="http://localhost:1/v1", model="m")

    def boom(*_a, **_k):
        raise ConnectionError("dropped")

    monkeypatch.setattr(d, "_run", boom)
    assert d.describe(img, "figure") is None
    assert d.calls == 1 and d.failures == 1


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


class _FakeDescriber:
    def describe(self, image_path, kind, context=""):
        return f"desc of {kind}"

    def model_for(self, kind):
        return "fake"


def test_describe_crops_routes_by_kind(tmp_path):
    from pdf2md.pipeline import _describe_crops
    from pdf2md.schema import BBox, Block, BlockType, FigureRef

    (tmp_path / "assets").mkdir()
    for name in ("p0.png", "e.png", "t.png"):
        (tmp_path / "assets" / name).write_bytes(b"png-" + name.encode())
    fig = FigureRef(block_id="#/pictures/0", page=1, bbox=BBox(0, 1, 1, 0), asset_path="assets/p0.png")
    eq = Block("#/texts/1", BlockType.EQUATION, "x", 1, extra={"crop_path": "assets/e.png"})
    tbl = Block("#/tables/0", BlockType.TABLE, "", 1, extra={"crop_path": "assets/t.png"})
    plain = Block("#/texts/2", BlockType.PARAGRAPH, "prose", 1)  # no crop -> skipped

    _describe_crops([fig], [eq, tbl, plain], _FakeDescriber(), tmp_path)
    assert fig.description == "desc of figure"
    assert eq.extra["transcribed"] == "desc of equation"  # equation feeds the hint
    assert tbl.extra["description"] == "desc of table"
    assert "description" not in plain.extra
    assert (tmp_path.parent / "describe_cache.json").exists()  # descriptions cached


def test_describe_cache_skips_reinference(tmp_path):
    from pdf2md.pipeline import _describe_crops
    from pdf2md.schema import BBox, FigureRef

    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "p0.png").write_bytes(b"png-bytes")

    class _Counting:
        calls = 0

        def describe(self, image_path, kind, context=""):
            self.calls += 1
            return "a description"

        def model_for(self, kind):
            return "m"

    d = _Counting()
    _describe_crops([FigureRef("#/p", 1, BBox(0, 1, 1, 0), asset_path="assets/p0.png")], [], d, tmp_path)
    fig2 = FigureRef("#/p", 1, BBox(0, 1, 1, 0), asset_path="assets/p0.png")
    _describe_crops([fig2], [], d, tmp_path)  # same crop bytes + model
    assert d.calls == 1 and fig2.description == "a description"  # second run hit the cache


def test_describe_does_not_override_math_ocr(tmp_path):
    from pdf2md.pipeline import _describe_crops
    from pdf2md.schema import Block, BlockType

    eq = Block("#/e", BlockType.EQUATION, "x", 1,
               extra={"crop_path": "assets/e.png", "transcribed": "from surya"})
    _describe_crops([], [eq], _FakeDescriber(), tmp_path)
    assert eq.extra["transcribed"] == "from surya"  # math-OCR transcription kept


def test_emit_description_block():
    from pdf2md.emit import _description

    assert _description(None) == ""
    out = _description("a flow chart of program calls")
    assert "AI-generated description" in out and "a flow chart of program calls" in out
