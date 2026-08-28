"""GROBID enrichment: TEI parsing and the never-fail client seam, tested against
canned documents. No service, no network."""

from __future__ import annotations

import urllib.error

from pdf2md.grobid import (
    fetch_grobid,
    is_alive,
    merge_grobid,
    parse_header_tei,
    parse_refs_tei,
)

_HEADER_TEI = b"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>Attention Is All You Need</title>
        <author><persName><forename>Ashish</forename><surname>Vaswani</surname></persName>
          <affiliation><orgName>Google Brain</orgName></affiliation></author>
        <author><persName><forename>Noam</forename><surname>Shazeer</surname></persName></author>
        <author><persName><forename>Google</forename><surname>Brain</surname></persName>
          <affiliation><orgName>Google Brain</orgName></affiliation></author>
      </titleStmt>
      <publicationStmt><date type="published">2017-06-12</date></publicationStmt>
      <sourceDesc><biblStruct>
        <monogr><title level="m">NIPS</title>
          <imprint><date>2017</date></imprint></monogr>
        <idno type="DOI">10.5555/3295222.3295349</idno>
      </biblStruct></sourceDesc>
    </fileDesc>
    <profileDesc>
      <abstract><div><p>The dominant sequence transduction models are based on
      recurrent networks.</p></div></abstract>
      <textClass><keywords><term>machine translation</term><term> attention</term></keywords></textClass>
    </profileDesc>
  </teiHeader>
</TEI>
"""

_REFS_TEI = b"""<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <listBibl>
    <biblStruct xml:id="b0">
      <note type="raw">Jimmy Ba et al. 2016. Layer normalization.</note>
      <analytic><title>Layer normalization</title></analytic>
      <idno type="DOI">10.48550/arXiv.1607.06450</idno>
    </biblStruct>
    <biblStruct xml:id="b1">
      <analytic><title>Distributed representations of words</title>
        <author><persName><forename>Tomas</forename><surname>Mikolov</surname></persName></author>
      </analytic>
      <monogr><title level="j">NIPS</title><imprint><date>2013</date></imprint></monogr>
    </biblStruct>
  </listBibl>
</TEI>"""


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.status = 200
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_parse_header_tei_extracts_fields():
    header = parse_header_tei(_HEADER_TEI)
    assert header["title"] == "Attention Is All You Need"
    # Affiliations ride along when present; bare authors stay bare.
    assert header["authors"][0] == "Ashish Vaswani (Google Brain)"
    assert header["authors"][1] == "Noam Shazeer"
    # An institution parsed as a person whose "name" equals its own affiliation
    # is dropped, not rendered as "Google Brain (Google Brain)".
    assert len(header["authors"]) == 2
    assert header["doi"] == "10.5555/3295222.3295349"
    assert header["year"] == "2017"
    assert header["venue"] == "NIPS"
    assert header["abstract"].startswith("The dominant sequence")
    assert header["keywords"] == ["machine translation", "attention"]


def test_parse_refs_tei_prefers_raw_strings_and_keeps_dois():
    refs = parse_refs_tei(_REFS_TEI)
    assert [r["index"] for r in refs] == [1, 2]
    assert refs[0]["text"] == "Jimmy Ba et al. 2016. Layer normalization."
    assert refs[0]["doi"] == "10.48550/arXiv.1607.06450"
    # No raw note: flattened from the structured fields.
    assert refs[1]["text"] == "Tomas Mikolov. Distributed representations of words; NIPS 2013."


class _FakeOpener:
    """Routes requests to canned bodies and records what was asked for."""

    def __init__(self, responses: dict[str, bytes]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def __call__(self, request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else request
        self.calls.append(url)
        body = self.responses.get(url)
        if body is None:
            raise urllib.error.HTTPError(url, 500, "boom", {}, None)
        return _FakeResponse(body)


def test_fetch_grobid_posts_both_endpoints_and_returns_payload(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-fake")
    base = "http://localhost:8070"
    opener = _FakeOpener({
        f"{base}/api/processHeaderDocument": _HEADER_TEI,
        f"{base}/api/processReferences": _REFS_TEI,
    })
    result = fetch_grobid(pdf, base, opener=opener)
    assert result is not None
    assert result["header"]["title"] == "Attention Is All You Need"
    assert len(result["references"]) == 2
    assert sorted(result["tei"]) == [
        "data/grobid-header.tei.xml",
        "data/grobid-references.tei.xml",
    ]
    assert sorted(opener.calls) == [
        f"{base}/api/processHeaderDocument",
        f"{base}/api/processReferences",
    ]


def test_fetch_grobid_returns_none_when_service_down(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-fake")

    def dead(request, timeout=None):
        raise OSError("connection refused")

    assert fetch_grobid(pdf, "http://localhost:8070", opener=dead) is None


def test_is_alive_false_on_error():
    def dead(request, timeout=None):
        raise OSError("nope")

    assert is_alive("http://localhost:8070", opener=dead) is False


def test_merge_grobid_fills_gaps_never_overrides():
    # Measured failure mode (1706.03762v7): GROBID's header model latches onto
    # arXiv license boilerplate and returns it as title/year. The heuristics'
    # reading stands; GROBID contributes only what heuristics lack.
    meta = {"title": "Heuristic Title", "year": "2017", "doi": None}
    merged = merge_grobid(meta, {
        "title": "Provided proper attribution ... Attention Is All You Need",
        "year": "2023",
        "authors": ["Ashish Vaswani"],
        "abstract": "text",
    })
    assert merged["title"] == "Heuristic Title"
    assert merged["year"] == "2017"
    assert merged["authors"] == ["Ashish Vaswani"]
    assert merged["abstract"] == "text"
    assert merged.get("metadata_source") == "grobid"

    untouched = merge_grobid({"title": "T"}, {})
    assert untouched == {"title": "T"}       # no answer: no source stamp either


def test_merge_grobid_updates_ranked_evidence_without_overriding_local_selection():
    evidence = {
        "schema_version": 1,
        "method": "ranked-local-metadata-v1",
        "title": {
            "selected": {
                "value": "Local title", "score": 90, "quality": "high",
                "evidence": [{"source": "embedded_title"}], "penalties": [],
            },
            "alternatives": [],
            "rejected": [],
        },
        "authors": {"selected": None, "alternatives": [], "rejected": []},
    }

    merged = merge_grobid(
        {"title": "Local title", "authors": None, "metadata_evidence": evidence},
        {"title": "Model title", "authors": ["A. Author"]},
    )

    assert merged["title"] == "Local title"
    assert merged["metadata_evidence"]["title"]["alternatives"][-1][
        "not_selected_reason"
    ] == "fill_gaps_only_policy"
    assert merged["authors"] == ["A. Author"]
    assert merged["metadata_evidence"]["authors"]["selected"]["evidence"] == [
        {"source": "grobid_header"}
    ]
