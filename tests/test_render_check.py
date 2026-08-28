"""Render-back equation verification: mathtext drawing, ink-profile comparison,
and the block-wiring pass. Needs matplotlib (dev group); skipped without it."""

from __future__ import annotations

import pytest

pytest.importorskip("matplotlib")

from pdf2md.confidence import (
    _render_ready_latex,
    check_equation_renders,
    compare_render,
    render_latex,
)
from pdf2md.schema import BBox, Block, BlockType


def _crop(tmp_path, name: str, latex: str) -> str:
    """A source crop the honest way: rendered by the same engine we compare with."""
    path = tmp_path / name
    render_latex(latex).save(path)
    return str(path)


def test_sanitize_strips_presentation_and_maps_unicode():
    ready = _render_ready_latex(r"\text{RMSE} = 5 \tag{2} (3)")
    assert r"\mathrm{RMSE}" in ready and "tag" not in ready
    assert not ready.rstrip("$").endswith("(3)")
    assert _render_ready_latex(r"\begin{aligned} x \end{aligned}") is None


def test_self_render_agrees(tmp_path):
    # The crop IS the LaTeX's own rendering: profiles must match closely.
    latex = r"E = \frac{p^2}{2m}"
    verdict = compare_render(_crop(tmp_path, "self.png", latex), latex)
    assert verdict["verdict"] == "similar"
    assert verdict["score"] >= 0.8


def test_different_topology_disagrees(tmp_path):
    # A tall stacked-fraction chain vs a single-line sum: layout differs.
    crop = _crop(tmp_path, "frac.png", r"E = \frac{1}{2} m v^2 + \frac{3}{4}")
    verdict = compare_render(crop, r"x + y = z")
    assert verdict["verdict"] in ("dissimilar", "unclear")
    assert verdict["score"] < 0.7


def test_unrenderable_verdict():
    assert compare_render("whatever.png", r"\begin{aligned}x\end{aligned}")[
        "verdict"
    ] == "unrenderable"


def test_check_equation_renders_wires_extra_and_skips_unbacked(tmp_path):
    good = _crop(tmp_path, "good.png", r"\alpha + \beta = \gamma^2")
    blocks = [
        Block("#/e1", BlockType.EQUATION, r"\alpha + \beta = \gamma^2", 1,
              bbox=BBox(0, 10, 5, 0), extra={"crop_path": good}),
        Block("#/e2", BlockType.EQUATION, "", 1, extra={"crop_path": good}),   # no LaTeX
        Block("#/e3", BlockType.EQUATION, r"x = y", 1),                        # no crop
        Block("#/p", BlockType.PARAGRAPH, "prose", 1),
    ]
    assert check_equation_renders(blocks) == 1
    assert blocks[0].extra["render_check"]["verdict"] == "similar"
    for block in blocks[1:]:
        assert "render_check" not in block.extra


def test_text_layer_judged_equations_are_out_of_scope(tmp_path):
    # The render check targets equations the text layer could NOT judge: scans
    # (ocr flag) or unjudged ones. Where assess_equation answered, it stands —
    # display-equation crops (numbers, stacked lines) would only add noise.
    good = _crop(tmp_path, "good.png", r"\alpha + \beta = \gamma^2")
    scanned = Block("#/e1", BlockType.EQUATION, r"\alpha + \beta = \gamma^2", 1,
                    bbox=BBox(0, 10, 5, 0),
                    extra={"crop_path": good, "ocr": True})
    born_digital = Block("#/e2", BlockType.EQUATION, r"\alpha + \beta = \gamma^2", 1,
                         bbox=BBox(0, 10, 5, 0), extra={"crop_path": good},
                         confidence=0.25)
    assert check_equation_renders([scanned, born_digital], version_dir=tmp_path) == 1
    assert "render_check" in scanned.extra
    assert "render_check" not in born_digital.extra


def test_render_support_counts_and_stamps_extra():
    from pdf2md.confidence import check_equation_render_support

    blocks = [
        Block("#/e1", BlockType.EQUATION, r"\alpha + \beta = \gamma^2", 1),
        Block("#/e2", BlockType.EQUATION, r"\begin{aligned} x &= y \end{aligned}", 1),
        Block("#/p", BlockType.PARAGRAPH, "prose", 1),
    ]
    counts = check_equation_render_support(blocks)
    assert counts == {"supported": 1, "unsupported": 1}
    assert blocks[0].extra["render_support"] == "supported"
    assert blocks[1].extra["render_support"] == "unsupported"
    assert "render_support" not in blocks[2].extra
