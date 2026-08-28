"""Shared fixtures. The fast suite never invokes Docling; it builds synthetic
blocks and drives the pipeline stages directly."""

from __future__ import annotations

import pytest

from pdf2md.schema import (
    BBox,
    Block,
    BlockType,
    Document,
    FigureRef,
    TableData,
)


def mk_block(bid: str, btype: BlockType, text: str, page: int = 1, **extra) -> Block:
    return Block(id=bid, type=btype, text=text, page=page, extra=extra)


@pytest.fixture
def sample_blocks() -> list[Block]:
    return [
        mk_block("#/texts/0", BlockType.HEADING, "1 Introduction", 1),
        mk_block("#/texts/1", BlockType.PARAGRAPH, "Hello world.", 1),
        mk_block("#/texts/2", BlockType.HEADING, "1.1 Background", 1),
        mk_block("#/texts/3", BlockType.PARAGRAPH, "More text.", 1),
        mk_block("#/texts/4", BlockType.EQUATION, "E = mc^2", 2),
        mk_block("#/tables/0", BlockType.TABLE, "", 2),
        mk_block("#/pictures/0", BlockType.FIGURE, "", 2),
        mk_block("#/texts/5", BlockType.FOOTNOTE, "a footnote", 2),
        mk_block("#/texts/6", BlockType.PARAGRAPH, "", 2),  # empty → dropped + marker
    ]


@pytest.fixture
def sample_tables() -> list[TableData]:
    return [
        TableData(
            block_id="#/tables/0",
            page=2,
            bbox=None,
            gfm="Table 1: a caption\n\n| a | b |\n|---|---|\n| 1 | 2 |",
        )
    ]


@pytest.fixture
def sample_figures() -> list[FigureRef]:
    return [
        FigureRef(
            block_id="#/pictures/0",
            page=2,
            bbox=BBox(0, 10, 10, 0),
            caption="Figure 1",
            asset_path="assets/pictures_0_p2.png",
        )
    ]


@pytest.fixture
def sample_document(sample_blocks, sample_tables, sample_figures) -> Document:
    from pdf2md.structure import build_structure

    structure = build_structure(sample_blocks, None, title="Doc", page_count=2)
    return Document(
        doc_id="abc123def456789a",
        source_path="/x/Doc.pdf",
        source_sha256="abc123def456789a",
        version=1,
        page_count=2,
        sections=structure.root,
        blocks=sample_blocks,
        tables=sample_tables,
        figures=sample_figures,
    )
