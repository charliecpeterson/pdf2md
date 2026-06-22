"""The QA harness gates CI-style on hard invariants, so its regression logic and
the unbalanced-equation detector must be right — a wrong gate gives false
confidence. (Signal collection is file I/O, exercised by the real run.)"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "qa", Path(__file__).parent.parent / "scripts" / "qa.py"
)
qa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qa)


def test_unbalanced_equation_detection():
    md = "\n".join([
        "$$\nE = mc^2\n$$",                                  # balanced
        "$$\n\\frac { a } { b }\n$$",                        # balanced braces
        "$$\n\\left( a \\right) \\right)\n$$",               # two \right, one \left
        "$$\nE ( \\text {x + y\n$$",                         # unbalanced braces (no close)
    ])
    assert qa._unbalanced(md) == 2


def test_check_gates_only_on_hard_invariants():
    base = {"d.pdf": {"lossless": True, "dropped": 0, "ligature_residual": 0,
                      "unbalanced_eq": 0, "illegible": 0, "illegible_table_rows": 0,
                      "eq_image_backed": 5}}
    # image-backing dropping is drift, not a regression; lossless loss and a risen
    # dropped count are.
    cur = {"d.pdf": {"lossless": False, "dropped": 2, "ligature_residual": 0,
                     "unbalanced_eq": 0, "illegible": 0, "illegible_table_rows": 0,
                     "eq_image_backed": 1}}
    regressions = qa._check(cur, base)
    assert any("lossless" in r for r in regressions)
    assert any("dropped 0 -> 2" in r for r in regressions)
    assert not any("image_backed" in r for r in regressions)

    # an all-clean run regresses nothing.
    assert qa._check(base, base) == []


def test_check_gates_on_illegible_regression():
    # A font-decode regression (clean text turning to symbol-font garbage) must fail
    # the gate, not pass silently as it did before the legibility signal — for prose
    # blocks and, separately, for rendered table rows.
    base = {"d.pdf": {"lossless": True, "dropped": 0, "ligature_residual": 0,
                      "unbalanced_eq": 0, "illegible": 0, "illegible_table_rows": 0}}
    cur = {"d.pdf": {"lossless": True, "dropped": 0, "ligature_residual": 0,
                     "unbalanced_eq": 0, "illegible": 37, "illegible_table_rows": 8}}
    regressions = qa._check(cur, base)
    assert any("illegible 0 -> 37" in r for r in regressions)
    assert any("illegible_table_rows 0 -> 8" in r for r in regressions)
