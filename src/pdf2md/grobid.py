"""Optional GROBID enrichment for bibliographic metadata.

GROBID (https://github.com/kermitt2/grobid) parses scholarly-PDF headers and
reference lists far better than embedded-metadata heuristics: title, authors
with affiliations, abstract, keywords, DOI, and every reference string with its
structured fields. This module speaks its REST API with the stdlib only — two
POSTs per document (header, references) — and never fails a conversion: an
unreachable or erroring service logs a warning and the pipeline keeps the
heuristic metadata.

Like the describer/transcriber seams this is opt-in (`--grobid-url`) and the
service is external (docker `lfoppiano/grobid`). The raw TEI rides back so the
bundle keeps the parse itself, not just the extracted fields; `pdf2md` writes it
under `data/` next to the other derived artifacts.
"""

from __future__ import annotations

import copy
import re
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from pdf2md.logging import get_logger

log = get_logger("grobid")

# Written into the version dir when a GROBID pass succeeded; manifest and
# front-matter point at them.
HEADER_TEI_NAME = "data/grobid-header.tei.xml"
REFS_TEI_NAME = "data/grobid-references.tei.xml"

# Fields GROBID owns when it answered: the specialized parser beats embedded
# metadata heuristics on all of these for scholarly PDFs.
_MERGE_FIELDS = ("title", "authors", "year", "doi", "venue", "abstract", "keywords")


def merge_grobid(meta: dict, header: dict) -> dict:
    """Overlay GROBID's header fields onto the heuristic metadata. Fill-gaps only:
    GROBID's header model can latch onto arXiv license boilerplate (measured on
    1706.03762v7, where it returned the license text as title and its re-posting
    date as year), so an existing heuristic value is never overridden; GROBID
    contributes what heuristics can't reach — abstract, keywords, venue,
    structured authors, references. `metadata_source: grobid` records that it
    answered."""
    merged = copy.deepcopy(meta)
    for field in _MERGE_FIELDS:
        value = header.get(field)
        if value and not merged.get(field):
            merged[field] = value
    if any(merged.get(f) != meta.get(f) for f in _MERGE_FIELDS):
        merged["metadata_source"] = "grobid"
    evidence = merged.get("metadata_evidence")
    if evidence:
        for field in ("title", "authors"):
            value = header.get(field)
            if not value:
                continue
            record = {
                "value": value,
                "score": 80,
                "quality": "medium",
                "evidence": [{"source": "grobid_header"}],
                "penalties": [],
            }
            field_evidence = evidence[field]
            if not meta.get(field):
                field_evidence["selected"] = record
            elif value != meta.get(field):
                record["not_selected_reason"] = "fill_gaps_only_policy"
                field_evidence["alternatives"].append(record)
    return merged

_YEAR = re.compile(r"\b(19|20)\d{2}\b")
# Reference strings arrive as TEI <note type="raw"> when requested; otherwise
# flatten the structured fields to one readable line.
_TAG_JUNK = re.compile(r"<[^>]+>")


def is_alive(base_url: str, *, timeout: float = 5.0,
             opener=urllib.request.urlopen) -> bool:
    """GROBID's liveness endpoint — used by `pdf2md doctor --probe-vlm`."""
    try:
        with opener(f"{base_url.rstrip('/')}/api/isalive", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001 - probing is best-effort by contract
        return False


def fetch_grobid(pdf_path: Path, base_url: str, *, timeout: float = 60.0,
                 consolidate: int = 0, opener=urllib.request.urlopen) -> dict | None:
    """POST the PDF for header and references TEI. Returns
    `{"header": {...}, "references": [{index, text, doi?}], "tei": {name: bytes}}`,
    or None when the service is unreachable or unhappy. Never raises."""
    pdf_bytes = Path(pdf_path).read_bytes()
    header_xml = _post(f"{base_url.rstrip('/')}/api/processHeaderDocument",
                       pdf_bytes, {"consolidate": str(consolidate)},
                       timeout=timeout, opener=opener)
    refs_xml = _post(f"{base_url.rstrip('/')}/api/processReferences",
                     pdf_bytes, {"consolidate": str(consolidate),
                                 "includeRawCitations": "1"},
                     timeout=timeout, opener=opener)
    if header_xml is None:
        return None
    header = parse_header_tei(header_xml)
    if not header:
        return None
    return {
        "header": header,
        "references": parse_refs_tei(refs_xml) if refs_xml else [],
        "tei": {
            HEADER_TEI_NAME: header_xml,
            REFS_TEI_NAME: refs_xml or b"",
        },
    }


def _post(url: str, pdf_bytes: bytes, fields: dict[str, str], *,
          timeout: float, opener) -> bytes | None:
    body, boundary = _multipart(pdf_bytes, fields)
    request = urllib.request.Request(
        url, data=body, method="POST",
        # Without this GROBID 0.8 content-negotiates to BibTeX; the parsers
        # below speak TEI.
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Accept": "application/xml"},
    )
    try:
        with opener(request, timeout=timeout) as resp:
            return resp.read() if resp.status == 200 else None
    except Exception as exc:  # noqa: BLE001 - the seam's contract: degrade loudly, don't fail
        log.warning("GROBID %s failed (%s); keeping heuristic metadata", url, exc)
        return None


def _multipart(pdf_bytes: bytes, fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = "pdf2md-grobid-boundary"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\""
            f"\r\n\r\n{value}\r\n".encode()
        )
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"input\";"
        f" filename=\"document.pdf\"\r\nContent-Type: application/pdf\r\n\r\n".encode()
        + pdf_bytes + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), boundary


def parse_header_tei(xml_bytes: bytes) -> dict:
    """The fields the bundle carries: title, authors, year, doi, venue, abstract,
    keywords. Missing elements simply stay absent."""
    try:
        root = ET.fromstring(_strip_decl(xml_bytes))
    except ET.ParseError as exc:
        log.warning("GROBID returned unparsable header TEI: %s", exc)
        return {}
    out: dict = {}

    title = root.findtext(".//{*}titleStmt/{*}title")
    if title:
        out["title"] = _clean(title)

    authors = []
    for author in root.findall(".//{*}fileDesc//{*}author"):
        # Person names only: an institutional author's orgName re-appears as its
        # own affiliation, and keeping it would render "Google Brain (Google Brain)".
        if author.find(".//{*}persName") is None:
            continue
        name = _person_name(author)
        if not name:
            continue
        orgs = [_clean(o.text) for o in author.findall(".//{*}affiliation//{*}orgName")]
        if any(name.casefold() == (o or "").casefold() for o in orgs):
            # GROBID parsed an institution as a person (forename "Google",
            # surname "Brain"; measured on 1706.03762v7).
            continue
        org = next((o for o in orgs if o), None)
        authors.append(f"{name} ({org})" if org else name)
    if authors:
        out["authors"] = authors

    doi = root.findtext(".//{*}idno[@type='DOI']")
    if doi:
        out["doi"] = doi.strip()

    date_text = ""
    node = root.find(".//{*}publicationStmt//{*}date[@type='published']")
    if node is None:
        node = root.find(".//{*}publicationStmt//{*}date")
    if node is not None:
        date_text = " ".join(node.itertext())
    if not date_text:  # fall back to the sourceDesc bibl's imprint date
        date_text = root.findtext(".//{*}sourceDesc//{*}imprint//{*}date") or ""
    year = _YEAR.search(date_text or "")
    if year:
        out["year"] = year.group(0)

    venue = root.findtext(".//{*}monogr/{*}title[@level='j']") \
        or root.findtext(".//{*}monogr/{*}title[@level='m']")
    if venue:
        out["venue"] = _clean(venue)

    abstract = " ".join(
        p_text.strip() for p_text in
        ("".join(p.itertext()) for p in root.findall(".//{*}profileDesc//{*}abstract//*"))
        if p_text.strip()
    ) or None
    abstract = _clean(abstract) if abstract else None
    if abstract:
        out["abstract"] = abstract

    keywords = [
        _clean(term.text or "") for term in root.findall(".//{*}keywords//{*}term")
        if term.text and term.text.strip()
    ]
    if keywords:
        out["keywords"] = keywords
    return out


def parse_refs_tei(xml_bytes: bytes) -> list[dict]:
    """One record per reference: the raw citation string when GROBID was asked
    for it, else the flattened structured fields; DOI when the record has one."""
    if not xml_bytes:
        return []
    try:
        root = ET.fromstring(_strip_decl(xml_bytes))
    except ET.ParseError as exc:
        log.warning("GROBID returned unparsable reference TEI: %s", exc)
        return []
    refs: list[dict] = []
    for i, bibl in enumerate(root.findall(".//{*}listBibl/{*}biblStruct"), start=1):
        raw = bibl.findtext("{*}note[@type='raw']")
        text = _clean(raw) if raw else _flatten_bibl(bibl)
        if not text:
            continue
        ref: dict = {"index": i, "text": text}
        doi = bibl.findtext(".//{*}idno[@type='DOI']")
        if doi:
            ref["doi"] = doi.strip()
        refs.append(ref)
    return refs


def _flatten_bibl(bibl) -> str:
    bits = [
        " ".join(t.itertext()).strip()
        for t in bibl.iter()
        if t.tag.endswith("}title") and "".join(t.itertext()).strip()
    ]
    names = [_person_name(a) for a in bibl.findall(".//{*}author")]
    people = ", ".join(n for n in names if n)
    date = bibl.findtext(".//{*}imprint//{*}date") or ""
    lead = f"{people}. " if people else ""
    tail = f" {date.strip()}." if date.strip() else ""
    return _clean(lead + "; ".join(bits) + tail)


def _person_name(author) -> str | None:
    pers = author.find("{*}persName") if author.find("{*}persName") is not None else \
        author.find(".//{*}persName")
    if pers is None:
        org = author.findtext(".//{*}orgName")
        return _clean(org) if org else None
    given = " ".join((n.text or "").strip() for n in pers.findall("{*}forename")
                     if n.text)
    surname = (pers.findtext("{*}surname") or "").strip()
    name = " ".join(x for x in (given, surname) if x)
    return _clean(name) or None


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    text = unicodedata.normalize("NFKC", text)
    text = _TAG_JUNK.sub("", text)  # safety net for nested markup fed by mistake
    text = " ".join(text.split())
    return text or None


def _strip_decl(xml_bytes: bytes) -> bytes:
    # ElementTree handles the XML declaration; DOCTYPE with external DTDs it does not.
    start = xml_bytes.find(b"<")
    end = xml_bytes.rfind(b"?>")
    if xml_bytes[start:start + 5] == b"<?xml" and 0 < end < 200:
        head = xml_bytes[start:end + 2]
        if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
            return xml_bytes[end + 2:]
    return xml_bytes
