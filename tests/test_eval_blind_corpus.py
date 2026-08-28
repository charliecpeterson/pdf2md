"""The blind-corpus audit separates structural evidence from semantic accuracy."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPTS = Path(__file__).parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location(
    "eval_blind_corpus", SCRIPTS / "eval_blind_corpus.py"
)
blind = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(blind)


def test_blind_corpus_reports_accounting_artifacts_and_review_separately(tmp_path):
    source_dir = tmp_path / "sources"
    output_dir = tmp_path / "out"
    source_dir.mkdir()
    source = source_dir / "paper.pdf"
    source.write_bytes(b"pdf")
    source_hash = blind._sha256(source)
    version = output_dir / source_hash[:16] / "v1"
    version.mkdir(parents=True)
    (version / "document.md").write_text("# Paper\n")
    (version / "profile.json").write_text(json.dumps({
        "contents": "document.md",
        "tables": 2,
        "figures": 1,
        "equations": 3,
    }))
    (version / "provenance.json").write_text(json.dumps({
        "source_sha256": source_hash,
        "page_count": 4,
        "coverage": {
            "total_blocks": 5,
            "emitted": 4,
            "cropped": 1,
            "flagged": 0,
            "dropped": 0,
            "flags": [{"reason": "image authoritative"}],
        },
    }))
    corpus = {
        "documents": [{
            "id": "paper",
            "sha256": source_hash,
            "pages": 4,
        }]
    }

    report = blind.evaluate(source_dir, output_dir, corpus)

    assert report["summary"] == {
        "documents": 1,
        "sources_valid": 1,
        "conversions": 1,
        "accounted_for": 1,
        "structurally_complete": 1,
        "content_present": 1,
        "documents_requiring_review": 1,
        "total_review_flags": 1,
    }
    assert report["documents"][0]["source_matches_output"]
    assert report["documents"][0]["page_count_matches"]
