"""The labelled corpora name real files, and every label agrees on which file.

Two claims, deliberately separated because only one of them is checkable
everywhere. That each source name maps to exactly one hash across the label
files is a property of the labels alone, and a disagreement there is a labelling
bug that would silently score two different PDFs as one document. That the file
is on disk with that hash needs the corpus, and 43 of the 47 sources are
copyrighted journal PDFs that cannot be redistributed. Asserting the second
unconditionally is what made the whole suite fail on any machine but the
maintainer's, including CI.

Point `PDF2MD_CORPUS` at a directory holding them to run the on-disk half; it
skips otherwise. `docs/qa-corpus.md` says what the corpus is.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_CORPUS = Path(os.environ.get("PDF2MD_CORPUS", _ROOT))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve(source: str) -> Path:
    """A corpus source, wherever the corpus is. Tracked fixtures stay in the tree."""
    in_tree = _ROOT / source
    return in_tree if in_tree.is_file() else _CORPUS / Path(source).name


def _require(path: Path, source: str) -> None:
    if not path.is_file():
        pytest.skip(f"corpus file not available: {source} (set PDF2MD_CORPUS)")


def _labelled_sources() -> dict[str, set[str]]:
    baseline = json.loads((_ROOT / "tests" / "qa_baseline.json").read_text())
    accuracy = json.loads((_ROOT / "tests" / "accuracy_labels.json").read_text())
    equations = json.loads((_ROOT / "tests" / "equation_labels.json").read_text())

    hashes_by_source: dict[str, set[str]] = {}
    for record in list(baseline.values()) + accuracy + equations:
        source_hash = record.get("source_sha256")
        assert source_hash and len(source_hash) == 64, record["source"]
        hashes_by_source.setdefault(record["source"], set()).add(source_hash)
    return hashes_by_source


def test_every_label_file_agrees_on_one_hash_per_source():
    """Needs no corpus: two hashes for one name means two documents share a name."""
    for source, source_hashes in _labelled_sources().items():
        assert len(source_hashes) == 1, source


def test_every_active_qa_source_matches_its_recorded_hash():
    for source, source_hashes in _labelled_sources().items():
        source_path = _resolve(source)
        _require(source_path, source)
        assert _sha256(source_path) == next(iter(source_hashes)), source


def test_every_scanned_numeric_source_exists_and_matches_its_labels():
    manifest = json.loads((_ROOT / "tests" / "scanned_numeric_corpus.json").read_text())

    for case in manifest["cases"]:
        labels = json.loads((_ROOT / case["labels"]).read_text())
        for document in labels["documents"]:
            source_path = _resolve(document["source"])
            _require(source_path, document["source"])
            assert _sha256(source_path) == document["source_sha256"], document["source"]


def test_scan_degradation_ground_truth_matches_its_source():
    ground_truth = json.loads(
        (_ROOT / "tests" / "scan_degradation_ground_truth.json").read_text()
    )
    source_path = _resolve(ground_truth["source"])
    _require(source_path, ground_truth["source"])

    assert _sha256(source_path) == ground_truth["source_sha256"]
    assert sum(len(row["values"]) for row in ground_truth["rows"]) == 162


def test_scan_degradation_pdfs_match_their_manifests():
    """These artifacts are generated and tracked, so they need no corpus."""
    for stem, pages in (
        ("dolg-table-iii-scan-degradation", 12),
        ("dolg-table-iii-combined-ablation", 7),
    ):
        artifact = _ROOT / "output" / "pdf" / f"{stem}.pdf"
        manifest = json.loads(
            (_ROOT / "output" / "pdf" / f"{stem}.manifest.json").read_text()
        )

        assert artifact.is_file()
        assert _sha256(artifact) == manifest["corpus_sha256"]
        assert manifest["corpus_pdf"] == artifact.name
        assert len(manifest["variants"]) == pages


def test_multifamily_degradation_artifacts_match_frozen_corpus():
    corpus = json.loads(
        (_ROOT / "tests" / "multifamily_degradation_corpus.json").read_text()
    )

    for artifact in corpus["artifacts"].values():
        artifact_path = _resolve(artifact["path"])
        _require(artifact_path, artifact["path"])
        assert _sha256(artifact_path) == artifact["sha256"], artifact["path"]

    expected = corpus["expected"]
    assert (expected["families"], expected["variants"]) == (5, 11)
    assert expected["labelled_cells_per_variant"] == 42
    assert expected["primary"] == {
        "checked": 462,
        "agree": 398,
        "disagree": 1,
        "tool_refused": 63,
    }
