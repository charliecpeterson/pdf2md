"""The multi-pass seam is testable without the model: a fake transcriber drives
the pipeline hook and emit, and the HTML->LaTeX extraction is a pure function."""

from __future__ import annotations

from pdf2md.pipeline import _transcribe_equations
from pdf2md.schema import BBox, Block, BlockType
from pdf2md.transcribe import _latex_from_html


def test_latex_from_html():
    assert _latex_from_html(r"<p><math>\frac { a } { b }</math></p>") == r"\frac { a } { b }"
    assert _latex_from_html("<p>just prose</p>") == "just prose"  # no math -> stripped text
    assert _latex_from_html(r"<math>a</math> x <math>b</math>") == "a b"  # joined


def test_transcribe_only_image_backed_equations():
    blocks = [
        Block(id="#/e1", type=BlockType.EQUATION, text="bad", page=1, bbox=BBox(0, 1, 1, 0),
              extra={"crop_path": "assets/e1.png"}),
        Block(id="#/e2", type=BlockType.EQUATION, text="trusted", page=1, bbox=BBox(0, 1, 1, 0)),
        Block(id="#/p", type=BlockType.PARAGRAPH, text="prose", page=1,
              extra={"crop_path": "assets/p.png"}),
    ]
    seen = []

    def fake_crop(b):
        seen.append(b.id)
        return r"\rho = 8\pi\nu^2/c^3"

    _transcribe_equations(blocks, fake_crop)
    assert blocks[0].extra["transcribed"] == r"\rho = 8\pi\nu^2/c^3"  # image-backed eq
    assert "transcribed" not in blocks[1].extra                       # trusted eq, no crop
    assert "transcribed" not in blocks[2].extra                       # not an equation
    assert seen == ["#/e1"]                                           # only the image-backed eq


def test_emit_prefers_transcribed_hint():
    from pdf2md.emit import _Ctx, _render_block
    from pdf2md.schema import CoverageStatus

    ctx = _Ctx(depth_of={}, tables={}, figures={})
    eq = Block(id="#/e", type=BlockType.EQUATION, text="garbled c^5", page=1, confidence=0.0,
               extra={"crop_path": "assets/e.png", "transcribed": r"\rho = c^3",
                      "text_layer": "ignored", "ordered": True})
    text, status, _ = _render_block(eq, ctx, [])
    assert "re-transcribed from the image" in text
    assert r"\rho = c^3" in text and "ignored" not in text  # transcription wins over text-layer
    assert status == CoverageStatus.CROPPED
