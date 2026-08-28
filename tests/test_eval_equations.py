"""Equation evaluation must reject missing and source-mismatched evidence."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


_spec = importlib.util.spec_from_file_location(
    "eval_equations", Path(__file__).parent.parent / "scripts" / "eval_equations.py"
)
evaluation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(evaluation)
ROOT = Path(__file__).parent.parent


def _run_check(tmp_path: Path, monkeypatch, labels: list[dict]) -> None:
    labels_path = tmp_path / "labels.json"
    labels_path.write_text(json.dumps(labels))
    monkeypatch.setattr(
        sys,
        "argv",
        ["eval_equations.py", str(tmp_path), "--labels", str(labels_path), "--strict"],
    )
    evaluation.main()


def test_check_mode_fails_when_labelled_source_is_missing(tmp_path, monkeypatch):
    labels = [{"source": "missing.pdf", "block_id": "#/equation/1", "latex": "x", "note": "x"}]

    with pytest.raises(SystemExit, match="1"):
        _run_check(tmp_path, monkeypatch, labels)


def test_check_mode_fails_when_no_facts_run(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="1"):
        _run_check(tmp_path, monkeypatch, [])


def test_check_mode_fails_on_source_hash_mismatch(tmp_path, monkeypatch):
    version_dir = tmp_path / "document" / "v1"
    version_dir.mkdir(parents=True)
    (version_dir / "provenance.json").write_text(json.dumps({
        "source_path": "document.pdf",
        "source_sha256": "different",
        "blocks": [{
            "id": "#/equation/1",
            "type": "equation",
            "text": "x",
            "extra": {},
        }],
    }))
    labels = [{
        "source": "document.pdf",
        "source_sha256": "expected",
        "block_id": "#/equation/1",
        "latex": "x",
        "note": "equation",
    }]

    with pytest.raises(SystemExit, match="1"):
        _run_check(tmp_path, monkeypatch, labels)


def test_check_mode_accepts_matching_source_and_equation(tmp_path, monkeypatch):
    version_dir = tmp_path / "document" / "v1"
    version_dir.mkdir(parents=True)
    (version_dir / "provenance.json").write_text(json.dumps({
        "source_path": "document.pdf",
        "source_sha256": "expected",
        "blocks": [{
            "id": "#/equation/1",
            "type": "equation",
            "text": "x",
            "extra": {},
        }],
    }))
    labels = [{
        "source": "document.pdf",
        "source_sha256": "expected",
        "block_id": "#/equation/1",
        "latex": "x",
        "note": "equation",
    }]

    _run_check(tmp_path, monkeypatch, labels)


def test_check_mode_finds_equation_in_whole_page_transcription(tmp_path, monkeypatch):
    version_dir = tmp_path / "document" / "v1"
    version_dir.mkdir(parents=True)
    (version_dir / "provenance.json").write_text(json.dumps({
        "source_path": "document.pdf",
        "source_sha256": "expected",
        "blocks": [{
            "id": "#/page/3",
            "page": 3,
            "text": "Prose\n\n$$E_i - E_j = h\\nu \\tag{1-8}$$",
            "extra": {"text_source": "vlm-page"},
        }],
    }))
    labels = [{
        "source": "document.pdf",
        "source_sha256": "expected",
        "block_id": "#/old/equation/1",
        "page": 3,
        "latex": "E_i - E_j = h\\nu",
        "note": "page equation",
    }]

    _run_check(tmp_path, monkeypatch, labels)


def test_labelled_equation_rejects_paragraph_at_stale_id():
    blocks = {
        "#/stale": {
            "id": "#/stale",
            "page": 4,
            "type": "paragraph",
            "text": "not an equation",
        },
        "#/equation": {
            "id": "#/equation",
            "page": 4,
            "type": "equation",
            "text": "x = 1",
        },
    }

    block = evaluation._labelled_equation(blocks, {
        "block_id": "#/stale",
        "page": 4,
        "latex": "x = 1",
    })

    assert block["id"] == "#/equation"


def test_labelled_equation_without_page_refuses_wrong_block_type():
    blocks = {
        "#/stale": {
            "id": "#/stale",
            "page": 4,
            "type": "paragraph",
            "text": "x = 1",
        }
    }

    assert evaluation._labelled_equation(blocks, {
        "block_id": "#/stale",
        "latex": "x = 1",
    }) is None


def test_component_normalization_ignores_equivalent_equation_numbers():
    assert evaluation._component_norm(r"E_i-E_j=h\nu\quad(1-8)") == (
        evaluation._component_norm(r"E_i-E_j=h\nu\tag{1-8}")
    )


def test_component_normalization_preserves_bold_symbols():
    assert evaluation._component_norm(r"\mathbf{x}") != (
        evaluation._component_norm("x")
    )


def test_component_fact_count_is_exact_when_requested():
    metrics = evaluation._candidate_metrics("x + x", {
        "latex": "x + x",
        "components": {"sign": [{"latex": "+", "count": 2}]},
    })

    assert metrics["facts"] == [{
        "kind": "sign",
        "latex": "+",
        "match_rule": "exact_count",
        "expected_count": 2,
        "actual_count": 1,
        "exact": False,
    }]


def test_frozen_equation_component_corpus_matches_outputs():
    report = evaluation.evaluate(
        ROOT / "out" / "qa-current", ROOT / "tests" / "equation_labels.json"
    )

    assert evaluation.check_corpus(
        ROOT, ROOT / "tests" / "equation_component_corpus.json", report
    )
