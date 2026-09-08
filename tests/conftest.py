"""Shared fixtures. The fast suite never invokes Docling; it builds synthetic
blocks and drives the pipeline stages directly."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pdf2md.schema import (
    BBox,
    Block,
    BlockType,
    Document,
    FigureRef,
    TableData,
)

_ROOT = Path(__file__).parent.parent
# Where the journal PDFs live, and where their converted bundles live. 43 of the
# 47 labelled sources are copyrighted and cannot be redistributed, so the tests
# that read them or their output skip rather than fail on a clean clone. That is
# not the same as deselecting: with the corpus present they run automatically.
CORPUS_ROOT = Path(os.environ.get("PDF2MD_CORPUS", _ROOT))
BUNDLE_ROOT = Path(os.environ.get("PDF2MD_BUNDLES", _ROOT / "out"))


def has_corpus_bundles() -> bool:
    return BUNDLE_ROOT.is_dir() and any(BUNDLE_ROOT.glob("*/v*/provenance.json"))


needs_corpus_bundles = pytest.mark.skipif(
    not has_corpus_bundles(),
    reason="converted corpus not available (set PDF2MD_BUNDLES; see docs/qa-corpus.md)",
)


def has_corpus_pdfs() -> bool:
    """Most of the labelled sources resolve, not merely one.

    A few entries point at a bundle's own `source.pdf` inside the output tree,
    which is present whenever any conversion has been run — so "at least one
    resolves" is true on a machine with no corpus at all."""
    baseline = json.loads((_ROOT / "tests" / "qa_baseline.json").read_text())
    found = sum(
        (_ROOT / record["source"]).is_file()
        or (CORPUS_ROOT / Path(record["source"]).name).is_file()
        for record in baseline.values()
    )
    return found * 2 >= len(baseline)


needs_corpus_pdfs = pytest.mark.skipif(
    not has_corpus_pdfs(),
    reason="labelled source PDFs not available (set PDF2MD_CORPUS; see docs/qa-corpus.md)",
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
