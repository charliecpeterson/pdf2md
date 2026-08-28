"""The focused scan corpus changes exactly one degradation factor per page."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPTS = Path(__file__).parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location(
    "build_scan_degradation_corpus",
    SCRIPTS / "build_scan_degradation_corpus.py",
)
corpus = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(corpus)


def test_combined_ablation_removes_exactly_one_factor():
    variants = corpus._combined_ablation_variants()
    factors = list(corpus._COMBINED_FACTORS)

    assert [variant["role"] for variant in variants] == [
        "control",
        "full_combination",
        *("leave_one_out" for _ in factors),
    ]
    assert variants[0]["factors"] == []
    assert variants[1]["factors"] == factors
    for variant, removed in zip(variants[2:], factors, strict=True):
        assert variant["removed_factor"] == removed
        assert variant["factors"] == [factor for factor in factors if factor != removed]
        assert variant["operations"] == corpus._combined_operations(
            tuple(variant["factors"])
        )
