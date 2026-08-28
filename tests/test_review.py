"""Review queues separate defects from valid image-backed representations."""

from __future__ import annotations

import json

from pdf2md.review import build_review_queue, write_review_files
from pdf2md.profile import build_profile, write_manifest, write_profile, write_readme
from pdf2md.schema import (
    Block,
    BlockType,
    CoverageFlag,
    CoverageReport,
    Document,
)
from pdf2md.structure import build_structure


def _document(blocks: list[Block], flags: list[CoverageFlag]) -> Document:
    structure = build_structure(blocks, None, title="D", page_count=max(b.page for b in blocks))
    doc = Document(
        "x" * 64,
        "/source.pdf",
        "x" * 64,
        1,
        max(block.page for block in blocks),
        structure.root,
        blocks=blocks,
    )
    doc.coverage = CoverageReport(doc.doc_id, len(blocks), 0, len(blocks), 0, 0, flags=flags)
    return doc


def test_intentional_equation_crops_do_not_create_actions():
    blocks = [
        Block(
            f"#/e/{index}",
            BlockType.EQUATION,
            "",
            index // 4 + 1,
            extra={"crop_path": f"assets/equation-{index}.png"},
        )
        for index in range(1848)
    ]
    flags = [
        CoverageFlag(
            block.id,
            block.page,
            "equation: image is authoritative",
            "marker",
            disposition="source_dependent",
            severity="none",
            content_impact="low",
        )
        for block in blocks
    ]

    doc = _document(blocks, flags)
    queue = build_review_queue(doc)

    assert queue["counts"] == {
        "action_required": 0,
        "source_dependent": 1848,
        "informational": 0,
    }
    assert not doc.coverage.needs_review


def test_review_queue_sorts_illegible_prose_before_source_dependence(tmp_path):
    blocks = [
        Block("#/e", BlockType.EQUATION, "", 1, extra={"crop_path": "assets/e.png"}),
        Block("#/p/late", BlockType.PARAGRAPH, "unreadable", 9),
        Block("#/p/early", BlockType.PARAGRAPH, "unreadable", 3),
        Block("#/p/mid", BlockType.PARAGRAPH, "unreadable", 6),
    ]
    flags = [
        CoverageFlag(
            "#/e", 1, "equation: image is authoritative", "marker",
            disposition="source_dependent", severity="none", content_impact="low",
        ),
        *[
            CoverageFlag(
                block.id, block.page, "illegible text layer", "marker",
                severity="high", content_impact="high",
            )
            for block in blocks[1:]
        ],
    ]
    queue = build_review_queue(_document(blocks, flags))

    assert queue["counts"] == {
        "action_required": 3,
        "source_dependent": 1,
        "informational": 0,
    }
    assert [item["page"] for item in queue["items"][:3]] == [3, 6, 9]
    assert queue["items"][3]["disposition"] == "source_dependent"

    markdown_path, json_path = write_review_files(tmp_path, queue)
    assert json.loads(json_path.read_text())["counts"] == queue["counts"]
    markdown = markdown_path.read_text()
    assert markdown.index("illegible text layer") < markdown.index(
        "equation: image is authoritative"
    )


def test_review_counts_agree_across_bundle_entry_points(tmp_path):
    block = Block(
        "#/e", BlockType.EQUATION, "", 1,
        extra={"crop_path": "assets/e.png"},
    )
    flag = CoverageFlag(
        block.id, 1, "equation: image is authoritative", "marker",
        disposition="source_dependent", severity="none", content_impact="low",
    )
    doc = _document([block], [flag])
    queue = build_review_queue(doc)
    profile = build_profile(doc, metadata={"title": "D"}, review_queue=queue)
    markdown_files = [tmp_path / "document.md"]

    write_review_files(tmp_path, queue)
    write_profile(tmp_path, doc, profile, markdown_files)
    write_manifest(tmp_path, doc, {"title": "D"}, profile, markdown_files, {}, queue)
    write_readme(tmp_path, doc, {"title": "D"}, profile, markdown_files)

    expected = {"action_required": 0, "source_dependent": 1, "informational": 0}
    assert json.loads((tmp_path / "profile.json").read_text())["review_counts"] == expected
    assert json.loads((tmp_path / "manifest.json").read_text())["quality"]["review_counts"] == expected
    assert json.loads((tmp_path / "review.json").read_text())["counts"] == expected
    assert "Source-dependent entries: 1." in (tmp_path / "README.md").read_text()
