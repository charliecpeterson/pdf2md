"""Passages keep retrieval context stable and source-addressable."""

from __future__ import annotations

import json

from jsonschema import Draft202012Validator

from pdf2md.passages import build_passages, load_passage_schema, write_passages
from pdf2md.passage_split import split_passage_text
from pdf2md.passage_tokenizer import load_passage_tokenizer
from pdf2md.schema import (
    BBox,
    Block,
    BlockType,
    CoverageFlag,
    CoverageReport,
    CoverageStatus,
    Document,
    FigureRef,
    Section,
    SectionKind,
    TableData,
)


class _WordTokenizer:
    id = "test-whitespace-v1"
    model_max_tokens = None

    def count(self, text: str) -> int:
        return len(text.split())


def _document(blocks: list[Block], *, doc_id: str = "a" * 64) -> Document:
    root = Section(
        "root",
        "Book",
        0,
        SectionKind.FRONT_MATTER,
        1,
        children=[Section(
            "chapter-1",
            "Chapter 1",
            1,
            SectionKind.CHAPTER,
            1,
            block_ids=[block.id for block in blocks],
        )],
    )
    doc = Document(
        doc_id,
        "/books/source.pdf",
        doc_id,
        1,
        max(block.page for block in blocks),
        root,
        blocks=blocks,
    )
    doc.coverage = CoverageReport(
        doc_id,
        total_blocks=len(blocks),
        emitted=len(blocks),
        cropped=0,
        flagged=0,
        dropped=0,
    )
    return doc


def _passages(doc: Document) -> list[dict]:
    emission_index = {
        block.id: {"markdown": "01_chapter-1.md", "text": block.text}
        for block in doc.blocks
    }
    return build_passages(
        doc,
        {"title": "Example Book", "authors": ["A. Author"]},
        [],
        {},
        emission_index=emission_index,
    )


def test_passages_are_stable_across_one_block_edit():
    before = _document([
        Block("#/p1", BlockType.PARAGRAPH, "first paragraph", 1,
              BBox(10, 100, 200, 80)),
        Block("#/p2", BlockType.PARAGRAPH, "second paragraph", 1,
              BBox(10, 70, 200, 50)),
    ])
    after = _document([
        Block("#/p1", BlockType.PARAGRAPH, "first paragraph", 1,
              BBox(10, 100, 200, 80)),
        Block("#/p2", BlockType.PARAGRAPH, "revised second paragraph", 1,
              BBox(10, 70, 200, 50)),
    ], doc_id="b" * 64)

    old = _passages(before)
    new = _passages(after)

    assert [record["id"] for record in old] == [record["id"] for record in new]
    assert old[0]["content_hash"] == new[0]["content_hash"]
    assert old[1]["content_hash"] != new[1]["content_hash"]
    assert old[0]["next_id"] == old[1]["id"]
    assert old[1]["previous_id"] == old[0]["id"]
    assert old[0]["previous_id"] is None and old[1]["next_id"] is None


def test_passage_carries_context_source_review_and_authority():
    equation = Block(
        "#/e",
        BlockType.EQUATION,
        "E = mc^2",
        2,
        BBox(20, 300, 220, 260),
        coverage_status=CoverageStatus.CROPPED,
        extra={"crop_path": "assets/equation.png"},
        engine="mineru",
    )
    doc = _document([equation])
    doc.coverage.flags.append(CoverageFlag(
        equation.id,
        2,
        "equation: image is authoritative",
        "marker",
        disposition="source_dependent",
        severity="none",
        content_impact="low",
    ))

    passage = _passages(doc)[0]

    assert passage["document"] == {
        "id": "a" * 64,
        "key": "81d00dfc1279b891",
        "title": "Example Book",
        "authors": ["A. Author"],
        "language": "und",
    }
    assert [section["title"] for section in passage["section_breadcrumb"]] == [
        "Book",
        "Chapter 1",
    ]
    assert passage["content_types"] == ["equation"]
    assert passage["markdown"] == "01_chapter-1.md"
    assert passage["sources"] == [{
        "block_id": "#/e",
        "page": 2,
        "bbox": {"x0": 20, "y0": 300, "x1": 220, "y1": 260},
        "source_page": "../source.pdf#page=2",
        "role": "primary",
    }]
    assert passage["authority"] == "source_image"
    assert passage["review"] == {
        "needs_review": False,
        "dispositions": ["source_dependent"],
    }
    assert passage["assets"] == [{
        "path": "assets/equation.png",
        "type": "source_crop",
        "provenance": "source_render",
    }]
    assert passage["display_text"] == "$$\nE = mc^2\n$$"
    assert passage["retrieval_text"].endswith("\n\n$$\nE = mc^2\n$$")
    assert passage["tokenizer"]["id"] == "pdf2md-unicode-lexical-v1"
    assert passage["tokenizer"]["count"] > 0
    assert passage["tokenizer"]["count"] <= passage["tokenizer"]["limit"]


def test_passage_retrieval_context_names_semantic_role():
    block = Block("#/methods", BlockType.PARAGRAPH, "We optimized each structure.", 1)
    doc = _document([block])

    passage = build_passages(
        doc,
        {"title": "Example Book", "authors": []},
        [],
        {},
        emission_index={block.id: {"markdown": "document.md", "text": block.text}},
        section_roles={"chapter-1": "methods"},
    )[0]

    assert "Semantic role: methods" in passage["retrieval_text"]


def test_passage_schema_validates_docling_and_mineru_records(tmp_path):
    schema = load_passage_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    blocks = [
        Block("#/docling", BlockType.PARAGRAPH, "Docling text", 1,
              BBox(0, 10, 10, 0), engine="docling"),
        Block("#/mineru", BlockType.PARAGRAPH, "MinerU text", 1,
              BBox(20, 10, 30, 0), engine="mineru"),
    ]
    doc = _document(blocks)
    records = _passages(doc)
    for record in records:
        validator.validate(record)

    passages_path, schema_path, count = write_passages(
        tmp_path,
        doc,
        {"title": "Example Book", "authors": ["A. Author"]},
        [tmp_path / "document.md"],
        {},
        emission_index={
            block.id: {"markdown": "document.md", "text": block.text}
            for block in blocks
        },
    )
    assert count == 2
    assert len(passages_path.read_text().splitlines()) == 2
    assert json.loads(schema_path.read_text()) == schema


def test_prose_splits_on_paragraph_and_sentence_boundaries_after_context():
    text = (
        "First sentence has four words. Second sentence also has four.\n\n"
        "Third paragraph stays intact."
    )
    tokenizer = _WordTokenizer()
    parts = split_passage_text(
        text,
        BlockType.PARAGRAPH,
        lambda part: f"fixed context words\n\n{part}",
        tokenizer,
        10,
    )

    assert parts == [
        "First sentence has four words.",
        "Second sentence also has four.",
        "Third paragraph stays intact.",
    ]
    assert all(tokenizer.count(f"fixed context words\n\n{part}") <= 10 for part in parts)


def test_lists_and_code_split_only_between_lines_when_lines_fit():
    tokenizer = _WordTokenizer()
    for content_type, text in (
        (BlockType.LIST, "- alpha beta\n- gamma delta\n- epsilon zeta"),
        (BlockType.CODE, "alpha = one\nbeta = two\ngamma = three"),
    ):
        parts = split_passage_text(
            text,
            content_type,
            lambda part: f"context words\n{part}",
            tokenizer,
            8,
        )

        assert parts == ["\n".join(text.splitlines()[:2]), text.splitlines()[2]]
        assert all(tokenizer.count(f"context words\n{part}") <= 8 for part in parts)


def test_table_continuations_repeat_caption_and_header_without_splitting_rows():
    caption = Block(
        "#/caption", BlockType.CAPTION, "Table 1. Energies in hartree.", 1,
        BBox(10, 200, 200, 180),
    )
    table_block = Block(
        "#/table", BlockType.TABLE, "", 1, BBox(10, 175, 200, 80)
    )
    doc = _document([caption, table_block])
    doc.tables = [TableData(
        table_block.id,
        1,
        table_block.bbox,
        "| State | Energy (Eh) |\n| --- | --- |\n"
        "| 1s | -0.50 |\n| 2s | -0.12 |\n| 2p | -0.11 |",
    )]
    tokenizer = _WordTokenizer()
    records = build_passages(
        doc,
        {"title": "Book"},
        [],
        {},
        tokenizer=tokenizer,
        max_tokens=36,
    )
    table_records = [record for record in records if record["sources"][0]["block_id"] == "#/table"]

    assert len(table_records) == 3
    for record, row in zip(table_records, ("| 1s | -0.50 |", "| 2s | -0.12 |", "| 2p | -0.11 |")):
        assert record["display_text"].startswith(
            "Table caption: Table 1. Energies in hartree.\n\n"
            "| State | Energy (Eh) |\n| --- | --- |"
        )
        assert record["display_text"].endswith(row)
        assert record["tokenizer"]["count"] <= 36
        assert record["content_types"] == ["table", "caption"]
        assert [source["block_id"] for source in record["sources"]] == [
            "#/table", "#/caption",
        ]


def test_equation_and_figure_passages_carry_nearby_explanatory_sources():
    equation_context = Block(
        "#/equation-context",
        BlockType.PARAGRAPH,
        "The rest energy follows from mass equivalence.",
        1,
        BBox(10, 250, 200, 230),
    )
    equation = Block(
        "#/equation", BlockType.EQUATION, "E = mc^2", 1,
        BBox(50, 220, 150, 200),
    )
    figure_context = Block(
        "#/figure-context",
        BlockType.PARAGRAPH,
        "Figure 2 compares the resulting energy levels.",
        1,
        BBox(10, 180, 200, 160),
    )
    figure = Block(
        "#/figure", BlockType.FIGURE, "", 1, BBox(20, 150, 190, 40)
    )
    doc = _document([equation_context, equation, figure_context, figure])
    doc.figures = [FigureRef(
        figure.id,
        1,
        figure.bbox,
        caption="Figure 2. Energy levels",
        caption_bbox=BBox(20, 38, 190, 25),
        asset_path="assets/figure.png",
    )]

    records = _passages(doc)
    equation_record = next(
        record for record in records if record["sources"][0]["block_id"] == equation.id
    )
    figure_record = next(
        record for record in records if record["sources"][0]["block_id"] == figure.id
    )

    assert equation_record["display_text"].startswith(
        "Explanatory context: The rest energy follows from mass equivalence."
    )
    assert equation_record["content_types"] == ["equation", "paragraph"]
    assert [source["block_id"] for source in equation_record["sources"]] == [
        equation.id, equation_context.id,
    ]
    assert figure_record["display_text"].startswith(
        "Referring text: Figure 2 compares the resulting energy levels."
    )
    assert figure_record["content_types"] == ["figure", "caption", "paragraph"]
    assert [source["block_id"] for source in figure_record["sources"]] == [
        figure.id, figure.id, figure_context.id,
    ]
    assert [source["role"] for source in figure_record["sources"]] == [
        "primary", "caption", "context",
    ]


def test_huggingface_passage_tokenizer_loads_from_local_model(tmp_path):
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from transformers import PreTrainedTokenizerFast

    backend = Tokenizer(WordLevel(
        {"[UNK]": 0, "alpha": 1, "beta": 2},
        unk_token="[UNK]",
    ))
    backend.pre_tokenizer = Whitespace()
    PreTrainedTokenizerFast(
        tokenizer_object=backend,
        unk_token="[UNK]",
    ).save_pretrained(tmp_path)

    tokenizer = load_passage_tokenizer(f"hf:{tmp_path}")

    assert tokenizer.id == f"huggingface:{tmp_path}"
    assert tokenizer.count("alpha beta") == 2


def test_passages_reject_limit_above_embedding_tokenizer_capacity():
    tokenizer = _WordTokenizer()
    tokenizer.model_max_tokens = 8
    doc = _document([
        Block("#/p", BlockType.PARAGRAPH, "short text", 1, BBox(0, 10, 10, 0))
    ])

    try:
        build_passages(doc, {"title": "Book"}, [], {}, tokenizer=tokenizer, max_tokens=9)
    except ValueError as exc:
        assert str(exc) == (
            "passage_max_tokens 9 exceeds tokenizer model_max_length 8"
        )
    else:
        raise AssertionError("passage limit above tokenizer capacity was accepted")


def test_passage_tokenizer_spec_rejects_unknown_kind():
    try:
        load_passage_tokenizer("unknown")
    except ValueError as exc:
        assert str(exc) == (
            "passage_tokenizer must be 'lexical' or 'hf:<model-or-local-path>'"
        )
    else:
        raise AssertionError("unknown tokenizer kind was accepted")
