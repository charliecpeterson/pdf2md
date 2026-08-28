"""Representation-aware conservation separates defects from image authority."""

from __future__ import annotations

from pdf2md.conservation import (
    annotate_conservation_warnings,
    conservation_review_flags,
    representation_accounting,
    token_accounting,
)
from pdf2md.schema import BBox, Block, BlockType, Document, Section, SectionKind


class _PageChars:
    def __init__(self, regions: dict[float, str]) -> None:
        self.regions = regions

    def text_region(self, bbox: BBox) -> str:
        return self.regions[bbox.x0]


class _Glyphs:
    def __init__(self, page: _PageChars) -> None:
        self.page = page

    def page_chars(self, page: int):
        return self.page if page == 1 else None


def _document(blocks: list[Block]) -> Document:
    root = Section("root", "D", 0, SectionKind.SECTION, 1, [block.id for block in blocks])
    return Document("x" * 64, "/source.pdf", "x" * 64, 1, 1, root, blocks=blocks)


def test_token_accounting_detects_exact_word_deletion_and_insertion():
    accounting = token_accounting("alpha beta 42", "alpha gamma 42")

    assert accounting["words"]["losses"] == {"beta": 1}
    assert accounting["words"]["additions"] == {"gamma": 1}
    assert accounting["numbers"]["losses"] == {}
    assert accounting["numbers"]["additions"] == {}


def test_token_accounting_separates_format_normalization():
    accounting = token_accounting("Value ALPHA −1,234.50", "value alpha -1234.50")

    assert accounting["words"]["expected_normalization"] == 2
    assert accounting["numbers"]["expected_normalization"] == 1
    assert not accounting["words"]["losses"] and not accounting["words"]["additions"]
    assert not accounting["numbers"]["losses"] and not accounting["numbers"]["additions"]


def test_representation_accounting_excludes_image_backed_equation_from_actions():
    prose = Block(
        "#/p", BlockType.PARAGRAPH, "alpha beta 42", 1,
        bbox=BBox(0, 10, 10, 0),
    )
    equation = Block(
        "#/e", BlockType.EQUATION, "", 1,
        bbox=BBox(20, 10, 30, 0),
        extra={"crop_path": "assets/e.png"},
    )
    emissions = {
        prose.id: {"markdown": "document.md", "text": "alpha gamma 42"},
        equation.id: {
            "markdown": "document.md",
            "text": "![equation](assets/e.png)",
        },
    }

    report = representation_accounting(
        _document([prose, equation]),
        _Glyphs(_PageChars({0: "alpha beta 42", 20: "delta 99"})),
        emissions,
    )

    assert report["categories"]["expected_source_dependent"] == {
        "words": 1,
        "numbers": 1,
    }
    assert report["categories"]["unexplained_loss"] == {"words": 1, "numbers": 0}
    assert report["categories"]["unexplained_addition"] == {"words": 1, "numbers": 0}
    assert report["blocks_with_unexplained_changes"] == 1
    assert {item["value"] for item in report["examples"]} == {"beta", "gamma"}
    assert all(item["block_id"] == "#/p" for item in report["examples"])
    assert all(item["page"] == 1 for item in report["examples"])
    assert all(item["bbox"] == {"x0": 0, "y0": 10, "x1": 10, "y1": 0}
               for item in report["examples"])
    assert all(item["emitted_artifact"] == "document.md" for item in report["examples"])

    flags = conservation_review_flags({"representation_aware": report})
    assert len(flags) == 1
    assert flags[0].block_id == "#/p"
    assert flags[0].disposition == "action_required"
    assert flags[0].severity == "medium"


def test_conservation_priority_puts_numbers_and_large_word_losses_first():
    def flag(words: int, numbers: int):
        return conservation_review_flags({
            "representation_aware": {
                "block_findings": [{
                    "block_id": "#/p",
                    "page": 2,
                    "unexplained_loss": {"words": words, "numbers": numbers},
                    "unexplained_addition": {"words": 0, "numbers": 0},
                }]
            }
        })[0]

    assert flag(2, 0).severity == "medium"
    assert flag(20, 0).severity == "high"
    assert flag(0, 1).severity == "high"


def test_conservation_warning_is_inserted_before_the_exact_emitted_block(tmp_path):
    path = tmp_path / "document.md"
    text = "# Paper\n\nrepeated paragraph\n\nrepeated paragraph\n"
    path.write_text(text)
    second = text.rindex("repeated paragraph")
    flag = conservation_review_flags({
        "representation_aware": {
            "block_findings": [{
                "block_id": "#/second",
                "page": 3,
                "unexplained_loss": {"words": 2, "numbers": 0},
                "unexplained_addition": {"words": 0, "numbers": 0},
            }]
        }
    })[0]

    count = annotate_conservation_warnings(
        tmp_path,
        [flag],
        {"#/second": {"markdown": "document.md", "start": second}},
    )

    annotated = path.read_text()
    assert count == 1
    assert annotated.count(flag.marker_text) == 1
    assert annotated.index(flag.marker_text) == second
    assert f"{flag.marker_text}\n\nrepeated paragraph" in annotated
    assert "action required (medium)" in flag.marker_text
    assert "[source page 3](../source.pdf#page=3)" in flag.marker_text
