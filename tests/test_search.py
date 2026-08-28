"""Literal passage search returns citation and review context from completed bundles."""

from __future__ import annotations

import hashlib
import json

from typer.testing import CliRunner

from pdf2md.cli import app
from pdf2md.search import find_passages, search_bundles


def _bundle(root, name: str, source: bytes, version: int, passages: list[dict]):
    document = root / f"{name}-{hashlib.sha256(source).hexdigest()[:8]}"
    document.mkdir(parents=True, exist_ok=True)
    (document / "source.pdf").write_bytes(source)
    bundle = document / f"v{version}"
    bundle.mkdir()
    (bundle / "provenance.json").write_text("{}")
    (bundle / "passages.jsonl").write_text(
        "".join(json.dumps(passage) + "\n" for passage in passages)
    )
    return bundle


def _passage(text: str, *, page: int = 2, review=None) -> dict:
    return {
        "id": "passage-1",
        "document": {"title": "Correlation-consistent basis sets"},
        "section_breadcrumb": [
            {"id": "root", "title": "Paper"},
            {"id": "methods", "title": "II. Basis sets"},
        ],
        "display_text": text,
        "sources": [{
            "block_id": "#/texts/4",
            "page": page,
            "source_page": f"../source.pdf#page={page}",
            "role": "primary",
        }],
        "authority": "text",
        "review": {
            "needs_review": bool(review),
            "dispositions": review or [],
        },
    }


def test_find_passages_normalizes_case_and_whitespace(tmp_path):
    bundle = _bundle(
        tmp_path,
        "paper",
        b"paper",
        1,
        [_passage(
            "Systematic convergence of both Hartree-Fock and correlation\n"
            "energies towards their respective CBS limits are observed.",
            review=["action_required"],
        )],
    )

    match = find_passages(bundle, "systematic convergence")[0]

    assert match.title == "Correlation-consistent basis sets"
    assert match.page == 2
    assert match.section == "II. Basis sets"
    assert match.authority == "text"
    assert match.review_dispositions == ("action_required",)
    assert match.source == f"{bundle.parent / 'source.pdf'}#page=2"
    assert "Systematic convergence" in match.excerpt


def test_long_excerpt_does_not_cut_words_at_its_edges(tmp_path):
    bundle = _bundle(
        tmp_path,
        "paper",
        b"paper",
        1,
        [_passage("prefixword " * 30 + "target phrase " + "suffixword " * 30)],
    )

    excerpt = find_passages(bundle, "target phrase")[0].excerpt

    assert excerpt.startswith("…prefixword ")
    assert excerpt.endswith(" suffixword…")


def test_corpus_search_uses_only_latest_completed_version(tmp_path):
    old = _bundle(tmp_path, "paper", b"paper", 1, [_passage("obsolete phrase")])
    latest = _bundle(tmp_path, "paper", b"paper", 2, [_passage("current phrase")])

    assert search_bundles(tmp_path) == [latest]
    assert find_passages(tmp_path, "obsolete phrase") == []
    assert [match.passage_id for match in find_passages(tmp_path, "current phrase")] == [
        "passage-1"
    ]
    assert old.exists()


def test_find_cli_prints_source_and_review_status(tmp_path):
    bundle = _bundle(
        tmp_path,
        "paper",
        b"paper",
        1,
        [_passage("Systematic convergence is observed.", review=["action_required"])],
    )

    result = CliRunner().invoke(
        app,
        ["find", str(bundle), "systematic convergence"],
    )

    assert result.exit_code == 0
    assert "Correlation-consistent basis sets" in result.stdout
    assert "p2  §II. Basis sets  authority=text  review=ACTION_REQUIRED" in result.stdout
    assert '"Systematic convergence is observed."' in result.stdout
    assert f"{bundle.parent / 'source.pdf'}#page=2" in result.stdout
    assert "1 match" in result.stdout


def test_find_cli_returns_one_when_nothing_matches(tmp_path):
    bundle = _bundle(tmp_path, "paper", b"paper", 1, [_passage("different text")])

    result = CliRunner().invoke(app, ["find", str(bundle), "missing phrase"])

    assert result.exit_code == 1
    assert "no matches for 'missing phrase'" in result.stdout
