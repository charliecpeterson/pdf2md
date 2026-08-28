"""Labelled metadata cases keep conservative title and author heuristics honest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf2md import doi_metadata, metadata
from pdf2md.schema import Block, BlockType


_CASES = json.loads((Path(__file__).parent / "metadata_corpus.json").read_text())


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case["name"])
def test_metadata_corpus(case, monkeypatch):
    monkeypatch.setattr(metadata, "_embedded", lambda _path: case["embedded"])
    blocks = [
        Block(
            f"#/texts/{index}",
            BlockType(item["type"]),
            item["text"],
            item["page"],
        )
        for index, item in enumerate(case["blocks"])
    ]

    extracted = metadata.extract_metadata("paper.pdf", blocks)

    assert extracted["title"] == case["expected_title"]
    assert extracted["authors"] == case["expected_authors"]
    assert extracted["metadata_evidence"]["method"] == "ranked-local-metadata-v1"


def test_ranked_title_prefers_repeated_clean_source_over_fragmented_cover(monkeypatch):
    monkeypatch.setattr(metadata, "_embedded", lambda _path: {})
    blocks = [
        Block("cover", BlockType.HEADING, "Relativistic Qu u an tum Chemistry", 1),
        Block(
            "title-2", BlockType.HEADING,
            "Introduction to Relativistic Quantum Chemistry", 2,
        ),
        Block(
            "title-4", BlockType.HEADING,
            "Introduction to Relativistic Quantum Chemistry", 4,
        ),
    ]

    extracted = metadata.extract_metadata("Intro-to_Relativistic-QC.pdf", blocks)
    evidence = extracted["metadata_evidence"]["title"]

    assert extracted["title"] == "Introduction to Relativistic Quantum Chemistry"
    assert evidence["selected"]["quality"] == "high"
    assert evidence["selected"]["evidence"] == [
        {"source": "front_heading", "page": 2, "block_id": "title-2"},
        {"source": "front_heading", "page": 4, "block_id": "title-4"},
        {"source": "repeated_heading", "pages": [2, 4], "occurrences": 2},
    ]
    assert evidence["alternatives"][0]["value"] == (
        "Relativistic Qu u an tum Chemistry"
    )
    assert evidence["alternatives"][0]["penalties"] == [
        "probable_glyph_fragmentation"
    ]


def test_embedded_title_and_authors_record_front_page_corroboration(monkeypatch):
    monkeypatch.setattr(metadata, "_embedded", lambda _path: {
        "Title": "ATKINS' PHYSICAL CHEMISTRY",
        "Author": "Peter Atkins . Julio De Paula",
    })
    blocks = [
        Block("title", BlockType.HEADING, "ATKINS' PHYSICAL CHEMISTRY", 1),
        Block("atkins", BlockType.HEADING, "Peter Atkins", 3),
        Block("de-paula", BlockType.HEADING, "Julio de Paula", 3),
    ]

    extracted = metadata.extract_metadata("atkins-physicalchemistry-8th.pdf", blocks)

    assert extracted["authors"] == ["Peter Atkins", "Julio De Paula"]
    author = extracted["metadata_evidence"]["authors"]["selected"]
    assert author["quality"] == "high"
    assert author["evidence"] == [{
        "source": "embedded_author",
        "printed_on_front_pages": ["Peter Atkins", "Julio De Paula"],
    }]
    assert [item["value"] for item in extracted["metadata_evidence"]["title"]["rejected"]] == [
        "Peter Atkins", "Julio de Paula",
    ]


def test_meaningful_filename_is_a_low_quality_fallback(monkeypatch):
    monkeypatch.setattr(metadata, "_embedded", lambda _path: {})

    extracted = metadata.extract_metadata("quantum-chemistry-notes.pdf", [])

    assert extracted["title"] == "quantum chemistry notes"
    assert extracted["metadata_evidence"]["title"]["selected"] == {
        "value": "quantum chemistry notes",
        "score": 32,
        "quality": "low",
        "evidence": [{"source": "filename"}],
        "penalties": [],
    }


def test_repeated_running_title_can_supply_a_missing_title(monkeypatch):
    monkeypatch.setattr(metadata, "_embedded", lambda _path: {})
    blocks = [
        Block(f"header-{page}", BlockType.PAGE_HEADER, "Spectral Methods Handbook", page)
        for page in (6, 7, 8)
    ]

    extracted = metadata.extract_metadata("paper.pdf", blocks)

    assert extracted["title"] == "Spectral Methods Handbook"
    assert extracted["metadata_evidence"]["title"]["selected"] == {
        "value": "Spectral Methods Handbook",
        "score": 65,
        "quality": "medium",
        "evidence": [{
            "source": "repeated_running_title",
            "pages": [6, 7, 8],
            "occurrences": 3,
        }],
        "penalties": [],
    }


def test_printed_paper_header_recovers_authors_citation_and_dates(monkeypatch):
    monkeypatch.setattr(metadata, "_embedded", lambda _path: {"Title": "Measured Paper"})
    blocks = [
        Block("title", BlockType.HEADING, "Measured Paper", 2),
        Block(
            "cite",
            BlockType.PARAGRAPH,
            "Cite as: J. Tests 151, 234112 (2019); doi: 10.1000/example "
            "Submitted: 23 September 2019 • Accepted: 21 November 2019 • "
            "Published Online: 18 December 2019",
            2,
        ),
        Block("author-1", BlockType.PARAGRAPH, "Joel Anderson, <sup>1,a)</sup>", 2),
        Block(
            "author-2",
            BlockType.PARAGRAPH,
            "Bryan Sundahl, <sup>1</sup> Robert Harrison,",
            2,
        ),
        Block("author-3", BlockType.PARAGRAPH, "and Gregory Beylkin <sup>2</sup>", 2),
        Block("affiliations", BlockType.HEADING, "AFFILIATIONS", 2),
    ]

    extracted = metadata.extract_metadata("paper.pdf", blocks)

    assert extracted["authors"] == [
        "Joel Anderson", "Bryan Sundahl", "Robert Harrison", "Gregory Beylkin",
    ]
    assert extracted["venue"] == "J. Tests"
    assert extracted["volume"] == "151"
    assert extracted["citation_locator"] == "234112"
    assert extracted["year"] == "2019"
    assert extracted["doi"] == "10.1000/example"
    assert extracted["publication_dates"] == {
        "submitted": "23 September 2019",
        "accepted": "21 November 2019",
        "published_online": "18 December 2019",
    }


def test_book_isbn_requires_a_valid_checksum(monkeypatch):
    monkeypatch.setattr(metadata, "_embedded", lambda _path: {})
    blocks = [
        Block("valid", BlockType.PARAGRAPH, "ISBN 978-0-13-468599-1", 2),
        Block("invalid", BlockType.PARAGRAPH, "ISBN 978-0-13-468599-2", 2),
        Block("edition", BlockType.PARAGRAPH, "Second Edition", 2),
    ]

    extracted = metadata.extract_metadata("book.pdf", blocks)

    assert extracted["isbn"] == ["9780134685991"]
    assert extracted["edition"] == "Second Edition"


def test_doi_registry_merge_corroborates_identity_and_adds_structured_fields():
    local = {
        "title": "Measured Paper",
        "authors": None,
        "year": "2019",
        "doi": "10.1000/example",
        "venue": "J. Tests",
        "metadata_evidence": {
            "title": metadata._field_evidence("Measured Paper", "front_heading"),
            "authors": {"selected": None, "alternatives": [], "rejected": []},
            "year": metadata._field_evidence("2019", "citation_line"),
            "doi": metadata._field_evidence("10.1000/example", "printed_doi"),
            "venue": metadata._field_evidence("J. Tests", "citation_line"),
        },
    }
    registry = {
        "type": "article-journal",
        "title": "Measured Paper",
        "author": [{"given": "A.", "family": "Author"}],
        "issued": {"date-parts": [[2019, 12, 18]]},
        "DOI": "10.1000/example",
        "container-title": "Journal of Tests",
        "volume": "151",
        "issue": "23",
        "page": "234112",
        "ISSN": ["1234-5678"],
        "abstract": "<jats:p>Measured &amp; checked.</jats:p>",
    }

    merged = doi_metadata.merge_doi_metadata(local, registry)

    assert merged["authors"] == ["A. Author"]
    assert merged["venue"] == "Journal of Tests"
    assert merged["volume"] == "151"
    assert merged["issue"] == "23"
    assert merged["pages"] == "234112"
    assert merged["issn"] == ["1234-5678"]
    assert merged["publication_dates"]["issued"] == "2019-12-18"
    assert merged["abstract"] == "Measured & checked."
    assert merged["registry_type"] == "article-journal"
    assert len(merged["metadata_evidence"]["title"]["selected"]["evidence"]) == 2
    assert merged["metadata_evidence"]["venue"]["alternatives"][0]["value"] == "J. Tests"


def test_doi_lookup_requests_csl_json_and_degrades_on_invalid_json():
    class Response:
        status = 200

        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    requests = []

    def valid(request, timeout=None):
        requests.append((request, timeout))
        return Response(b'{"DOI": "10.1000/example"}')

    record = doi_metadata.fetch_doi_metadata("10.1000/example", opener=valid)

    assert record == {"DOI": "10.1000/example"}
    assert requests[0][0].headers["Accept"] == "application/vnd.citationstyles.csl+json"
    assert requests[0][1] == 20.0

    assert doi_metadata.fetch_doi_metadata(
        "10.1000/example",
        opener=lambda request, timeout=None: Response(b"not json"),
    ) is None
