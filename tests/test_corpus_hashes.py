from __future__ import annotations

import hashlib
import json
from pathlib import Path


_ROOT = Path(__file__).parent.parent


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_every_active_qa_source_exists_and_matches_one_hash():
    baseline = json.loads((_ROOT / "tests" / "qa_baseline.json").read_text())
    accuracy = json.loads((_ROOT / "tests" / "accuracy_labels.json").read_text())
    equations = json.loads((_ROOT / "tests" / "equation_labels.json").read_text())

    records = list(baseline.values()) + accuracy + equations
    hashes_by_source: dict[str, set[str]] = {}
    for record in records:
        source = record["source"]
        source_hash = record.get("source_sha256")
        assert source_hash and len(source_hash) == 64, source
        hashes_by_source.setdefault(source, set()).add(source_hash)

    for source, source_hashes in hashes_by_source.items():
        assert len(source_hashes) == 1, source
        source_path = _ROOT / source
        assert source_path.is_file(), source
        assert _sha256(source_path) == next(iter(source_hashes)), source


def test_every_scanned_numeric_source_exists_and_matches_its_labels():
    manifest = json.loads((_ROOT / "tests" / "scanned_numeric_corpus.json").read_text())

    for case in manifest["cases"]:
        labels = json.loads((_ROOT / case["labels"]).read_text())
        for document in labels["documents"]:
            source_path = _ROOT / document["source"]
            assert source_path.is_file(), document["source"]
            assert _sha256(source_path) == document["source_sha256"], document["source"]


def test_scan_degradation_ground_truth_matches_its_source():
    ground_truth = json.loads(
        (_ROOT / "tests" / "scan_degradation_ground_truth.json").read_text()
    )
    source_path = _ROOT / ground_truth["source"]

    assert source_path.is_file()
    assert _sha256(source_path) == ground_truth["source_sha256"]
    assert sum(len(row["values"]) for row in ground_truth["rows"]) == 162


def test_scan_degradation_pdfs_match_their_manifests():
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
        artifact_path = _ROOT / artifact["path"]
        assert artifact_path.is_file(), artifact["path"]
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
