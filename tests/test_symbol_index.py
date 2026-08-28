"""Symbol indexing requires quoted local definitions and preserves overloads."""

from __future__ import annotations

import json

from pdf2md.symbol_index import build_symbol_index, write_symbol_index


def _passage(
    passage_id: str,
    section_id: str,
    section_title: str,
    content_type: str,
    text: str,
    page: int,
) -> dict:
    return {
        "id": passage_id,
        "markdown": f"{section_id}.md",
        "section_breadcrumb": [
            {"id": "root", "title": "Book"},
            {"id": section_id, "title": section_title},
        ],
        "content_types": [content_type],
        "display_text": text,
        "sources": [{
            "block_id": f"#/{passage_id}",
            "page": page,
            "bbox": None,
            "source_page": f"../source.pdf#page={page}",
            "role": "primary",
        }],
    }


def test_symbol_index_quotes_definitions_and_keeps_overloaded_symbols_local():
    passages = [
        _passage(
            "mechanics-definition", "mechanics", "Mechanics", "paragraph",
            "In this expression, where $E$ is the total energy.", 1,
        ),
        _passage(
            "mechanics-equation", "mechanics", "Mechanics", "equation",
            "$$ E = mc^2 $$", 2,
        ),
        _passage(
            "mechanics-context-copy", "mechanics", "Mechanics", "paragraph",
            "Explanatory context: In this expression, where $E$ is the total energy.", 2,
        ),
        _passage(
            "fields-definition", "fields", "Electromagnetism", "paragraph",
            "Here, where $E$ denotes the electric field.", 3,
        ),
        _passage(
            "fields-equation", "fields", "Electromagnetism", "equation",
            "$$ E = -\\nabla V $$", 4,
        ),
        _passage(
            "undefined", "fields", "Electromagnetism", "paragraph",
            "The symbol $q$ appears without a definition.", 4,
        ),
    ]

    index = build_symbol_index("a" * 64, passages)

    assert [entry["symbol"] for entry in index["entries"]] == ["E", "E"]
    assert [entry["scope"]["section_id"] for entry in index["entries"]] == [
        "fields", "mechanics",
    ]
    assert all(entry["meaning_status"] == "source_quoted" for entry in index["entries"])
    fields, mechanics = index["entries"]
    assert fields["definition"]["quote"] == (
        "Here, where $E$ denotes the electric field."
    )
    assert [item["passage_id"] for item in fields["occurrences"]] == [
        "fields-definition", "fields-equation",
    ]
    assert [item["passage_id"] for item in mechanics["occurrences"]] == [
        "mechanics-definition", "mechanics-equation", "mechanics-context-copy",
    ]


def test_symbol_index_is_deterministic_and_writes_empty_refusal(tmp_path):
    passage = _passage(
        "undefined", "chapter", "Chapter", "paragraph",
        "Variables occur here, but none is explicitly defined.", 1,
    )
    passages_path = tmp_path / "passages.jsonl"
    passages_path.write_text(json.dumps(passage) + "\n")

    first = write_symbol_index(tmp_path, "a" * 64, passages_path).read_text()
    second = write_symbol_index(tmp_path, "a" * 64, passages_path).read_text()
    index = json.loads(second)

    assert first == second
    assert index["method"]["inference"] == "none"
    assert index["entry_count"] == 0
    assert index["entries"] == []
