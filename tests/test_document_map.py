"""Document maps resolve hierarchy, passages, review state, and source regions."""

from __future__ import annotations

from pdf2md.document_map import build_document_map, write_document_map
from pdf2md.schema import Block, BlockType, Document, Section, SectionKind


def _passage(
    passage_id: str,
    block_id: str,
    page: int,
    markdown: str,
    breadcrumb: list[Section],
    content_type: str,
    *,
    authority: str = "text",
    dispositions: list[str] | None = None,
) -> dict:
    return {
        "id": passage_id,
        "markdown": markdown,
        "section_breadcrumb": [{"id": section.id} for section in breadcrumb],
        "content_types": [content_type],
        "sources": [{
            "block_id": block_id,
            "page": page,
            "bbox": {"x0": 10, "y0": 100, "x1": 200, "y1": 80},
            "source_page": f"../source.pdf#page={page}",
            "role": "primary",
        }],
        "authority": authority,
        "review": {
            "needs_review": "action_required" in (dispositions or []),
            "dispositions": dispositions or [],
        },
        "assets": ([{
            "path": "assets/equation.png",
            "type": "source_crop",
            "provenance": "source_render",
        }] if authority == "source_image" else []),
    }


def _fixture(tmp_path):
    chapter = Section(
        "chapter", "Chapter 1", 1, SectionKind.CHAPTER, 1,
        block_ids=["#/p", "#/e"],
    )
    bibliography = Section(
        "references", "References", 1, SectionKind.CHAPTER, 3,
        block_ids=["#/r"],
    )
    glossary = Section(
        "notation", "Notation and symbols", 1, SectionKind.APPENDIX, 4,
        block_ids=["#/g"],
    )
    root = Section(
        "root", "Book", 0, SectionKind.FRONT_MATTER, 1,
        children=[chapter, bibliography, glossary],
    )
    doc = Document(
        "a" * 64, "/books/source.pdf", "a" * 64, 1, 4, root,
        blocks=[
            Block("#/p", BlockType.PARAGRAPH, "text", 1),
            Block("#/e", BlockType.EQUATION, "E", 2),
            Block("#/r", BlockType.PARAGRAPH, "reference", 3),
            Block("#/g", BlockType.PARAGRAPH, "notation", 4),
        ],
    )
    passages = [
        _passage("passage-1", "#/p", 1, "01_chapter.md", [root, chapter], "paragraph"),
        _passage(
            "passage-2", "#/e", 2, "01_chapter.md", [root, chapter], "equation",
            authority="source_image", dispositions=["source_dependent"],
        ),
        _passage(
            "passage-3", "#/r", 3, "02_references.md",
            [root, bibliography], "paragraph",
        ),
        _passage(
            "passage-4", "#/g", 4, "03_notation.md", [root, glossary], "paragraph",
            dispositions=["action_required"],
        ),
    ]
    md_files = [
        tmp_path / name
        for name in ("index.md", "01_chapter.md", "02_references.md", "03_notation.md")
    ]
    for path in md_files:
        path.write_text(path.stem)
    return doc, passages, md_files


def test_document_map_pins_hierarchy_ranges_locations_and_source_dependence(tmp_path):
    doc, passages, md_files = _fixture(tmp_path)

    outline = build_document_map(doc, {"title": "Book"}, md_files, passages)

    assert outline["document"] == {
        "id": "a" * 64,
        "title": "Book",
        "pages": 4,
    }
    assert outline["section_count"] == 4
    assert outline["block_counts_by_content_type"] == {"equation": 1, "paragraph": 3}
    assert outline["passage_counts_by_content_type"] == {"equation": 1, "paragraph": 3}
    assert outline["outline"]["passage_ranges"] == [{
        "start_index": 0,
        "end_index": 3,
        "start_id": "passage-1",
        "end_id": "passage-4",
        "count": 4,
    }]
    chapter = outline["outline"]["children"][0]
    assert chapter["pages"] == {"start": 1, "end": 2}
    assert chapter["markdown"] == ["01_chapter.md"]
    assert chapter["block_counts_by_content_type"] == {"equation": 1, "paragraph": 1}
    assert chapter["passage_counts_by_content_type"] == {"equation": 1, "paragraph": 1}
    assert chapter["review_hotspots"] == [{
        "page": 2,
        "action_required": 0,
        "source_dependent": 1,
        "informational": 0,
        "total": 1,
    }]
    assert outline["named_locations"] == [
        {
            "type": "bibliography",
            "section_id": "references",
            "title": "References",
            "pages": {"start": 3, "end": 3},
            "markdown": ["02_references.md"],
        },
        {
            "type": "glossary",
            "section_id": "notation",
            "title": "Notation and symbols",
            "pages": {"start": 4, "end": 4},
            "markdown": ["03_notation.md"],
        },
    ]
    assert outline["source_dependent_regions"] == [{
        "passage_id": "passage-2",
        "markdown": "01_chapter.md",
        "content_types": ["equation"],
        "sources": passages[1]["sources"],
        "assets": passages[1]["assets"],
    }]


def test_document_map_files_and_nodes_resolve_to_existing_artifacts(tmp_path):
    doc, passages, md_files = _fixture(tmp_path)
    passages_path = tmp_path / "passages.jsonl"
    import json
    passages_path.write_text("".join(json.dumps(item) + "\n" for item in passages))

    path = write_document_map(tmp_path, doc, {"title": "Book"}, md_files, passages_path)
    outline = json.loads(path.read_text())

    passage_ids = {passage["id"] for passage in passages}
    for entry in outline["markdown_files"]:
        assert (tmp_path / entry["path"]).is_file()
        for passage_range in entry["passage_ranges"]:
            assert passage_range["start_id"] in passage_ids
            assert passage_range["end_id"] in passage_ids

    def check_node(node):
        assert 1 <= node["pages"]["start"] <= node["pages"]["end"] <= doc.page_count
        assert node["markdown"]
        assert all((tmp_path / name).is_file() for name in node["markdown"])
        for child in node["children"]:
            check_node(child)

    check_node(outline["outline"])
