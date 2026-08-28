from __future__ import annotations

from pdf2md.scripts import _score_line, apply_scripts


def test_overlay_wraps_subscript():
    scored = [("C", None), ("2", "sub"), ("H", None)]
    assert apply_scripts("C2H", scored) == "C<sub>2</sub>H"


def test_overlay_merges_adjacent_run():
    scored = [("x", None), ("1", "sup"), ("2", "sup")]
    assert apply_scripts("x12", scored) == "x<sup>12</sup>"


def test_overlay_escapes_html_but_not_its_tags():
    scored = [("<", None), ("2", "sub")]
    assert apply_scripts("<2", scored, escape=True) == "&lt;<sub>2</sub>"


def test_overlay_no_flags_is_identity():
    assert apply_scripts("hello", [(c, None) for c in "hello"]) == "hello"


def test_overlay_never_corrupts_text_on_misalignment():
    # scored doesn't line up with text; text characters must survive verbatim.
    out = apply_scripts("abc", [("x", "sub"), ("y", "sup")])
    assert out.replace("<sub>", "").replace("</sub>", "").replace("<sup>", "").replace("</sup>", "") == "abc"


def test_detect_subscript_from_geometry():
    # (char, left, bottom, right, top); the '2' is short and sits low.
    line = [("C", 0, 100, 8, 110), ("2", 8, 98, 12, 104), ("H", 12, 100, 20, 110)]
    flags = dict(_score_line(line))
    assert flags["2"] == "sub"
    assert flags["C"] is None


def test_detect_superscript_from_geometry():
    line = [("C", 0, 100, 8, 110), ("2", 8, 106, 12, 112), ("H", 12, 100, 20, 110)]
    flags = dict(_score_line(line))
    assert flags["2"] == "sup"
