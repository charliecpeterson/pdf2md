"""Agent metadata keeps document roles and references source-addressable."""

from __future__ import annotations

import json

from pdf2md.document_metadata import build_document_metadata, write_document_metadata
from pdf2md.schema import Block, BlockType, Document
from pdf2md.structure import build_structure


def _document(blocks: list[Block], *, pages: int, bookmarks=None, meta=None):
    meta = meta or {"title": "Example"}
    structure = build_structure(
        blocks,
        bookmarks,
        title=meta["title"],
        page_count=pages,
    )
    doc = Document(
        "a" * 64,
        "/library/source.pdf",
        "a" * 64,
        1,
        pages,
        structure.root,
        blocks=blocks,
    )
    return doc, structure


def test_paper_metadata_roles_and_numbered_references_are_auditable(tmp_path):
    blocks = [
        Block("title", BlockType.HEADING, "Measured Paper", 1),
        Block("abstract", BlockType.HEADING, "ABSTRACT", 1),
        Block("abstract-text", BlockType.PARAGRAPH, "Summary text.", 1),
        Block("intro", BlockType.HEADING, "I. INTRODUCTION", 1),
        Block("intro-text", BlockType.PARAGRAPH, "Context.", 1),
        Block("methods", BlockType.HEADING, "II. COMPUTATIONAL DETAILS", 2),
        Block("methods-text", BlockType.PARAGRAPH, "Procedure.", 2),
        Block("results", BlockType.HEADING, "III. RESULTS AND DISCUSSION", 3),
        Block("results-text", BlockType.PARAGRAPH, "Findings.", 3),
        Block("conclusion", BlockType.HEADING, "IV. CONCLUSIONS", 4),
        Block("conclusion-text", BlockType.PARAGRAPH, "Conclusion.", 4),
        Block("references", BlockType.HEADING, "REFERENCES", 5),
        Block("ref-1", BlockType.PARAGRAPH, "1 A. Author, First work (2020).", 5),
        Block("ref-2", BlockType.LIST, "<sup>2</sup> B. Author, Long work", 5),
        Block("ref-2b", BlockType.LIST, "continued title (2021).", 6),
        Block(
            "ref-3",
            BlockType.LIST,
            "3 C. Author, Third work. doi:10.1000/example",
            6,
        ),
    ]
    meta = {
        "title": "Measured Paper",
        "doi": "10.1000/paper",
        "venue": "J. Tests",
        "metadata_evidence": {},
    }
    doc, structure = _document(blocks, pages=6, meta=meta)

    artifact = build_document_metadata(
        doc,
        meta,
        section_source=structure.section_source,
    )

    assert artifact["document"]["kind"] == {
        "value": "journal_article",
        "verification": {
            "status": "corroborated",
            "evidence": [
                {"source": "bibliographic_field", "field": "doi"},
                {"source": "semantic_section", "role": "abstract"},
                {"source": "semantic_section", "role": "references"},
                {"source": "citation_line"},
            ],
        },
    }
    roles = {
        section["section_id"]: section["semantic_role"]
        for section in artifact["sections"]
    }
    assert roles["abstract"] == "abstract"
    assert roles["methods"] == "methods"
    assert roles["results"] == "results_and_discussion"
    assert roles["conclusion"] == "conclusions"
    assert roles["references"] == "references"

    references = artifact["references"]
    assert references["count"] == 3
    assert references["sections"] == [{
        "section_id": "references",
        "count": 3,
        "numbering": {
            "status": "sequence_complete",
            "observed": [1, 2, 3],
            "missing": [],
        },
    }]
    assert references["items"][1]["block_ids"] == ["ref-2", "ref-2b"]
    assert references["items"][1]["verification"]["evidence"] == [
        {"source": "source_text", "block_id": "ref-2"},
        {"source": "source_text", "block_id": "ref-2b"},
    ]
    assert references["items"][1]["text"].endswith("continued title (2021).")
    assert references["items"][2]["dois"] == ["10.1000/example"]

    path = write_document_metadata(tmp_path, artifact)
    assert json.loads(path.read_text()) == artifact


def test_long_book_uses_book_structure_and_marks_back_matter():
    blocks = [
        Block("chapter-1", BlockType.HEADING, "Chapter 1 Foundations", 1),
        Block("body-1", BlockType.PARAGRAPH, "Text.", 2),
        Block("bibliography", BlockType.HEADING, "Bibliography", 90),
        Block("book-ref", BlockType.PARAGRAPH, "A. Author. A Book. 2020.", 90),
        Block("index", BlockType.HEADING, "Index", 95),
        Block("index-text", BlockType.PARAGRAPH, "adaptive methods, 12", 95),
    ]
    bookmarks = [
        ("Chapter 1 Foundations", 0, 0),
        ("Bibliography", 89, 0),
        ("Index", 94, 0),
    ]
    meta = {
        "title": "Example Book",
        "isbn": ["9780134685991"],
        "metadata_evidence": {},
    }
    doc, structure = _document(
        blocks,
        pages=100,
        bookmarks=bookmarks,
        meta=meta,
    )

    artifact = build_document_metadata(
        doc,
        meta,
        section_source=structure.section_source,
    )

    assert artifact["document"]["kind"]["value"] == "book"
    roles = {
        section["title"]: section["semantic_role"]
        for section in artifact["sections"]
    }
    assert roles["Chapter 1 Foundations"] == "chapter"
    assert roles["Bibliography"] == "references"
    assert roles["Index"] == "index"
    assert artifact["references"]["items"][0]["text"] == "A. Author. A Book. 2020."
