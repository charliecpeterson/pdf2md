"""Source list identifiers are content, not a request to renumber the output."""

import json
from types import SimpleNamespace

import pytest

from pdf2md.chunks import write_chunks
from pdf2md.document_metadata import build_document_metadata
from pdf2md.emit import emit_document
from pdf2md.engine_state import load_engine_state, write_engine_state
from pdf2md.engines.base import EngineResult
from pdf2md.engines.docling import DoclingEngine
from pdf2md.passages import build_passages
from pdf2md.schema import Document
from pdf2md.structure import build_structure


def _item(marker, text, *, orig=None, label="list_item", ref="#/texts/1"):
    return SimpleNamespace(
        self_ref=ref, label=label, text=text, marker=marker,
        orig=orig if orig is not None else f"{marker or ''} {text}".strip(),
        enumerated=bool(marker),
        prov=[SimpleNamespace(page_no=1, bbox=SimpleNamespace(l=10, t=100, r=300, b=80))],
    )


def _translate(items):
    doc = SimpleNamespace(iterate_items=lambda: [(item, 0) for item in items])
    return DoclingEngine.__new__(DoclingEngine)._blocks(doc)


@pytest.mark.parametrize(("marker", "text", "orig", "expected"), [
    ("[17]", "Reference entry.", None, "[17] Reference entry."),
    ("(9)", "Checklist entry.", None, "(9) Checklist entry."),
    ("7.", "List entry.", None, "7. List entry."),
    ("iv)", "List entry.", None, "iv) List entry."),
    ("a)", "List entry.", None, "a) List entry."),
    ("[1]", "[1] Already retained.", "[1] Already retained.", "[1] Already retained."),
    ("1", "10 samples.", None, "1 10 samples."),
    ("1", "1 sample.", "1 1 sample.", "1 1 sample."),
    ("•", "Bullet entry.", None, "Bullet entry."),
    ("-", "Bullet entry.", None, "Bullet entry."),
    ("", "No identifier.", None, "No identifier."),
    (None, "No identifier.", None, "No identifier."),
    ("(9)", "", None, ""),
])
def test_adapter_preserves_identifiers_without_inventing_or_duplicating_them(
    marker, text, orig, expected,
):
    block, = _translate([_item(marker, text, orig=orig)])
    assert block.text == expected


def test_marker_like_attribute_on_non_list_text_is_not_prepended():
    block, = _translate([_item("[17]", "Ordinary prose.", label="text")])
    assert block.text == "Ordinary prose."


def test_older_list_item_without_marker_still_translates():
    item = _item(None, "Unnumbered entry.")
    del item.marker
    block, = _translate([item])
    assert block.text == "Unnumbered entry."
    assert block.extra == {}


@pytest.mark.parametrize(("marker", "rendered_marker"), [
    ("[17]", "[17]"), ("(9)", "(9)"), ("7.", r"7\."),
    ("7)", r"7\)"), ("[x]", r"\[x]"),
])
def test_identifier_survives_state_markdown_and_retrieval(tmp_path, marker, rendered_marker):
    blocks = _translate([_item(marker, "Source entry.")])
    write_engine_state(tmp_path, "a" * 64, EngineResult(blocks, [], [], {1: (600, 800)}))
    blocks = load_engine_state(tmp_path).blocks
    assert blocks[0].text == f"{marker} Source entry."
    structure = build_structure(blocks, None, title="Doc", page_count=1)
    doc = Document("a" * 64, "/source.pdf", "a" * 64, 1, 1, structure.root, blocks=blocks)
    emissions = {}
    paths, flags = emit_document(
        doc, structure, tmp_path, {"title": "Doc"}, {}, emission_index=emissions,
    )
    assert flags == []
    assert emissions[blocks[0].id]["text"] == f"- {rendered_marker} Source entry."
    assert f"\n- {rendered_marker} Source entry.\n" in paths[0].read_text()
    passages = build_passages(doc, {"title": "Doc"}, paths, {}, emission_index=emissions)
    assert len(passages) == 1
    assert passages[0]["display_text"] == f"{marker} Source entry."
    chunk_path = write_chunks(tmp_path, doc, paths, {}, emission_index=emissions)
    chunks = [json.loads(line) for line in chunk_path.read_text().splitlines()]
    assert len(chunks) == 1
    assert chunks[0]["text"] == f"{marker} Source entry."


def test_reference_gaps_and_cross_page_labels_are_not_renumbered(tmp_path):
    items = [
        _item(None, "References", label="section_header", ref="refs"),
        _item("[1]", "First reference.", ref="first"),
        _item("[3]", "Third reference.", ref="third"),
    ]
    items[-1].prov[0].page_no = 2
    blocks = _translate(items)
    structure = build_structure(blocks, None, title="Doc", page_count=2)
    doc = Document("a" * 64, "/source.pdf", "a" * 64, 1, 2, structure.root, blocks=blocks)
    paths, flags = emit_document(doc, structure, tmp_path, {"title": "Doc"}, {})
    assert flags == []
    md = paths[0].read_text()
    assert "- [1] First reference." in md
    assert "- [3] Third reference." in md
    assert "[2]" not in md
    metadata = build_document_metadata(doc, {"title": "Doc"},
                                       section_source=structure.section_source)
    refs = metadata["references"]
    assert [(r["ordinal"], r["pages"]) for r in refs["items"]] == [(1, [1]), (3, [2])]
    assert refs["sections"][0]["numbering"] == {
        "status": "sequence_gaps", "observed": [1, 3], "missing": [2],
    }
