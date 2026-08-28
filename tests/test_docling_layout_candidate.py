"""Pinned Docling layout runs must reject mutable or changed model artifacts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


_spec = importlib.util.spec_from_file_location(
    "docling_layout_candidate",
    Path(__file__).parent.parent / "scripts" / "docling_layout_candidate.py",
)
candidate_runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(candidate_runner)


def test_candidate_revision_must_be_an_immutable_commit(tmp_path):
    registry = tmp_path / "candidates.json"
    registry.write_text(json.dumps({
        "schema_version": 1,
        "candidates": [{
            "id": "docling-test",
            "revision": "main",
            "weight_sha256": "0" * 64,
        }],
    }))

    with pytest.raises(ValueError, match="commit hash"):
        candidate_runner._load_candidate(registry, "docling-test")


def test_weight_verification_pins_size_and_sha256(tmp_path):
    weights = tmp_path / "model.safetensors"
    weights.write_bytes(b"exact model")
    candidate = {
        "model_path": "",
        "weight_file": weights.name,
        "weight_bytes": len(b"exact model"),
        "weight_sha256": candidate_runner._sha256(weights),
    }

    verified = candidate_runner._verify_weights(candidate, tmp_path)

    assert verified["weight_bytes"] == 11
    assert verified["weight_sha256"] == candidate_runner._sha256(weights)
    weights.write_bytes(b"changed")
    with pytest.raises(ValueError, match="bytes != pinned"):
        candidate_runner._verify_weights(candidate, tmp_path)
