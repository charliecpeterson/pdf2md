"""Multi-family scoring refuses ambiguous structure and separates wrong values."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path


SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "eval_multifamily_degradation",
    SCRIPTS / "eval_multifamily_degradation.py",
)
evaluation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(evaluation)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_pinned_ground_truth_covers_five_families_and_42_cells():
    sources = json.loads(evaluation.DEFAULT_SOURCES.read_text())

    ground_truth = evaluation._ground_truth(evaluation.DEFAULT_SOURCES, sources)

    assert {family: len(records) for family, records in ground_truth.items()} == {
        "grasp-fixed-width": 10,
        "fischer-serif-superscripts": 12,
        "slater-curved-two-panel": 8,
        "grasp-scientific-notation": 8,
        "nasa-leading-dot": 4,
    }


def test_key_locator_requires_one_source_row_identity():
    assert evaluation._locate_key_cell(
        [("#/table/1", [["1", "0.25"], ["2", "0.50"]])],
        "1",
        0,
        1,
    ) == ("#/table/1", 0, 1, "0.25")
    assert evaluation._locate_key_cell(
        [("#/table/1", [["1", "0.25"], ["1", "0.50"]])],
        "1",
        0,
        1,
    ) is None


def test_evaluate_reports_wrong_value_separately_from_refusal(tmp_path, monkeypatch):
    sources_path = tmp_path / "sources.json"
    sources_path.write_text(json.dumps({
        "schema_version": 1,
        "families": [{"id": "family-a", "typography": "fixed"}],
    }))
    corpus_pdf = tmp_path / "corpus.pdf"
    corpus_pdf.write_bytes(b"controlled corpus")
    manifest_path = tmp_path / "corpus.manifest.json"
    manifest_path.write_text(json.dumps({
        "schema_version": 1,
        "sources_sha256": _sha256(sources_path),
        "corpus_pdf": corpus_pdf.name,
        "corpus_sha256": _sha256(corpus_pdf),
        "variants": [
            {"id": "clean", "factors": [], "role": "control"},
            {"id": "jpeg", "factors": ["jpeg"], "role": "isolated"},
        ],
        "pages": [
            {
                "page": 1,
                "family": "family-a",
                "variant": "clean",
                "factors": [],
                "role": "control",
            },
            {
                "page": 2,
                "family": "family-a",
                "variant": "jpeg",
                "factors": ["jpeg"],
                "role": "isolated",
            },
        ],
    }))
    version_dir = tmp_path / "v1"
    version_dir.mkdir()
    (version_dir / "provenance.json").write_text("{}\n")

    monkeypatch.setattr(evaluation, "_ground_truth", lambda path, sources: {
        "family-a": [{
            "id": "family-a:0",
            "family": "family-a",
            "locator": "key",
            "row_key": "1",
            "key_column": 0,
            "column_offset": 1,
            "expected": "0.25",
            "expected_kind": "numeric",
            "class": "decimal",
        }]
    })
    monkeypatch.setattr(evaluation, "_page_tables", lambda path: {
        1: [("#/table/1", [["1", "0.25"]])],
        2: [("#/table/2", [["1", "9.25"]])],
    })
    monkeypatch.setattr(evaluation, "_resolved_cells", lambda path: {})

    report = evaluation.evaluate(version_dir, sources_path, manifest_path)

    assert report["primary"] == {
        "checked": 2,
        "agree": 1,
        "disagree": 1,
        "tool_refused": 0,
    }
    assert report["by_factor"]["jpeg"]["primary"] == {
        "checked": 1,
        "agree": 0,
        "disagree": 1,
        "tool_refused": 0,
    }
    assert report["clean_transitions"] == {
        "family-a:jpeg": {"agree_to_disagree": 1}
    }


def test_check_corpus_pins_artifacts_and_checked_result(tmp_path, monkeypatch):
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("pinned evidence\n")
    checked = {
        "provenance_sha256": "a" * 64,
        "sources_sha256": "b" * 64,
        "corpus_manifest_sha256": "c" * 64,
        "corpus_sha256": "d" * 64,
        "runtime_sha256": "e" * 64,
        "families": 1,
        "variants": 1,
        "labelled_cells_per_variant": 1,
        "primary": {"checked": 1, "agree": 1, "disagree": 0, "tool_refused": 0},
        "reader": {"checked": 1, "agree": 0, "disagree": 0, "tool_refused": 1},
        "best": {"checked": 1, "agree": 1, "disagree": 0, "tool_refused": 0},
        "by_family": {},
        "by_variant": {},
        "by_factor": {},
        "by_role": {},
        "clean_transitions": {},
    }
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps({
        "schema_version": 1,
        "artifacts": {
            "evidence": {"path": artifact.name, "sha256": _sha256(artifact)},
        },
        "expected": checked,
    }))
    monkeypatch.setattr(evaluation, "ROOT", tmp_path)

    assert evaluation.check_corpus(corpus_path, checked)
    changed = checked | {
        "primary": {"checked": 1, "agree": 0, "disagree": 1, "tool_refused": 0}
    }
    assert not evaluation.check_corpus(corpus_path, changed)
