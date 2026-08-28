"""Build agent-facing document identity, semantic sections, and references.

The artifact keeps source observations separate from parser and registry evidence.
Verification states describe agreement and traceability, not probabilities.
"""

from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
import json
import re
from pathlib import Path

from pdf2md.schema import Block, BlockType, Document, Section, SectionKind


METADATA_NAME = "metadata.json"
METADATA_SCHEMA_VERSION = 1

_DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
_REFERENCE_LABEL = re.compile(
    r"^\s*(?:<sup>\s*)?\[?(\d+)\]?(?:\s*</sup>)?\s*[.)]?\s*",
    re.IGNORECASE,
)
_HEADING_PREFIX = re.compile(
    r"^\s*(?:(?:[ivxlcdm]+|\d+(?:\.\d+)*|[a-z])\s*[.):-]\s+)",
    re.IGNORECASE,
)
_PUBLISHER_MATERIAL = re.compile(
    r"articles you may be interested in|special topics open for submissions|"
    r"recommended articles|related content",
    re.IGNORECASE,
)

_FIELD_NAMES = (
    "title",
    "subtitle",
    "authors",
    "editors",
    "year",
    "doi",
    "venue",
    "publisher",
    "volume",
    "issue",
    "pages",
    "article_number",
    "citation_locator",
    "issn",
    "isbn",
    "edition",
    "publication_dates",
    "abstract",
    "keywords",
    "license",
)


def _sections(root: Section) -> list[Section]:
    ordered: list[Section] = []

    def walk(section: Section) -> None:
        ordered.append(section)
        for child in section.children:
            walk(child)

    walk(root)
    return ordered


def _section_block_ids(section: Section) -> set[str]:
    return {
        *section.block_ids,
        *(
            block_id
            for child in section.children
            for block_id in _section_block_ids(child)
        ),
    }


def _normalized_heading(title: str) -> str:
    return _HEADING_PREFIX.sub("", " ".join(title.split())).casefold().strip(" .:-")


def _matched_role(section: Section, document_title: str, document_kind: str) -> tuple[str, str]:
    title = _normalized_heading(section.title)
    if section.id == "root":
        return "document", "root_section"
    if title == " ".join(document_title.split()).casefold().strip(" .:-"):
        return "document_front_matter", "document_title_match"
    if _PUBLISHER_MATERIAL.search(title):
        return "publisher_material", "publisher_material_heading"
    if re.fullmatch(r"abstract|summary", title):
        return "abstract", "abstract_heading"
    if re.fullmatch(r"affiliations?|author information", title):
        return "author_information", "author_heading"
    if re.fullmatch(r"introduction|overview", title):
        return "introduction", "introduction_heading"
    if re.search(
        r"materials? and methods?|methodology|methods?|computational details|experimental",
        title,
    ):
        return "methods", "methods_heading"
    if re.search(r"results? and discussion", title):
        return "results_and_discussion", "combined_results_heading"
    if re.fullmatch(r"results?", title):
        return "results", "results_heading"
    if re.fullmatch(r"discussion", title):
        return "discussion", "discussion_heading"
    if re.search(r"conclusions?|concluding remarks|summary and conclusions", title):
        return "conclusions", "conclusion_heading"
    if re.search(r"acknowledg(?:e)?ments?|funding", title):
        return "acknowledgments", "acknowledgment_heading"
    if re.search(r"bibliograph|references|works cited|further reading", title):
        return "references", "references_heading"
    if re.fullmatch(r"contents|table of contents", title):
        return "table_of_contents", "contents_heading"
    if re.search(r"preface|foreword", title):
        return "preface", "preface_heading"
    if re.search(r"glossar|nomenclature|notation|symbols|abbreviations", title):
        return "glossary", "glossary_heading"
    if re.fullmatch(r"index", title):
        return "index", "index_heading"
    if section.kind is SectionKind.APPENDIX or title.startswith("appendix"):
        return "appendix", "appendix_structure"
    if document_kind == "book":
        if section.kind is SectionKind.PART:
            return "part", "book_structure"
        if section.kind is SectionKind.CHAPTER:
            return "chapter", "book_structure"
    if re.fullmatch(r"background|related work|literature review", title):
        return "background", "background_heading"
    if re.fullmatch(r"theory|theoretical background", title):
        return "theory", "theory_heading"
    return "body", "unclassified_heading"


def semantic_roles(
    root: Section,
    *,
    document_title: str,
    document_kind: str,
) -> dict[str, dict]:
    roles: dict[str, dict] = {}

    def walk(section: Section, inherited: str | None = None) -> None:
        role, rule = _matched_role(section, document_title, document_kind)
        if role == "body" and inherited in {"references", "appendix", "publisher_material"}:
            role = inherited
            rule = "inherited_from_parent"
        roles[section.id] = {
            "role": role,
            "verification": {
                "status": "structural" if rule in {
                    "root_section", "book_structure", "appendix_structure",
                } else "rule_match" if rule != "unclassified_heading" else "unclassified",
                "evidence": [{
                    "source": "section_tree",
                    "section_id": section.id,
                    "heading": section.title,
                    "rule": rule,
                }],
            },
        }
        for child in section.children:
            walk(child, role)

    walk(root)
    return roles


def _kind_record(
    doc: Document,
    meta: dict,
    section_source: str,
    roles: dict[str, dict],
) -> dict:
    evidence: list[dict] = []
    role_values = {item["role"] for item in roles.values()}
    if meta.get("doi"):
        evidence.append({"source": "bibliographic_field", "field": "doi"})
    if "abstract" in role_values:
        evidence.append({"source": "semantic_section", "role": "abstract"})
    if "references" in role_values:
        evidence.append({"source": "semantic_section", "role": "references"})
    if meta.get("citation_locator") or meta.get("venue"):
        evidence.append({"source": "citation_line"})
    if meta.get("isbn"):
        evidence.append({"source": "bibliographic_field", "field": "isbn"})
    if meta.get("registry_type"):
        evidence.append({"source": "doi_registry", "type": meta["registry_type"]})
    if section_source == "bookmarks" and doc.page_count >= 40:
        evidence.append({"source": "structure", "value": "long_bookmarked_document"})

    headings = "\n".join(section.title for section in _sections(doc.sections))
    registry_type = str(meta.get("registry_type") or "").casefold()
    if re.search(r"\b(?:dissertation|thesis)\b", headings, re.IGNORECASE):
        value = "thesis"
    elif (
        registry_type in {"book", "monograph", "book-chapter", "chapter"}
        or meta.get("isbn")
        or any(item.get("value") == "long_bookmarked_document" for item in evidence)
    ):
        value = "book_chapter" if "chapter" in registry_type else "book"
    elif registry_type in {"article-journal", "journal-article"} or (
        meta.get("doi") and ({"abstract", "references"} & role_values)
    ):
        value = "journal_article"
    elif re.search(r"\btechnical report\b|\breport no\.?\b", headings, re.IGNORECASE):
        value = "report"
    else:
        value = "unknown"

    supporting = len(evidence)
    return {
        "value": value,
        "verification": {
            "status": (
                "corroborated" if supporting >= 2
                else "single_source" if supporting == 1
                else "missing"
            ),
            "evidence": evidence,
        },
    }


def _field_verification(field: str, meta: dict) -> dict:
    value = meta.get(field)
    evidence = (meta.get("metadata_evidence") or {}).get(field) or {}
    selected = evidence.get("selected") or {}
    sources = selected.get("evidence") or []
    unique_sources = {item.get("source") for item in sources if item.get("source")}
    if not value:
        status = "missing"
    elif selected.get("conflict"):
        status = "conflict"
    elif len(unique_sources) >= 2:
        status = "corroborated"
    else:
        status = "single_source"
    return {
        "value": value,
        "alternatives": evidence.get("alternatives") or [],
        "rejected": evidence.get("rejected") or [],
        "verification": {
            "status": status,
            "evidence": sources,
            **({"quality": selected["quality"]} if selected.get("quality") else {}),
        },
    }


def _reference_blocks(doc: Document, section: Section) -> list[Block]:
    block_ids = _section_block_ids(section)
    return [
        block for block in doc.blocks
        if block.id in block_ids
        and block.type not in {BlockType.HEADING, BlockType.PAGE_HEADER, BlockType.PAGE_FOOTER}
        and block.text.strip()
    ]


def _dois(text: str) -> list[str]:
    return list(dict.fromkeys(
        match.group(0).rstrip(".,;)") for match in _DOI.finditer(text)
    ))


def _local_references(doc: Document, roles: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    sections = [
        section for section in _sections(doc.sections)
        if roles[section.id]["role"] == "references"
        and not any(
            roles[child.id]["role"] == "references" for child in section.children
        )
    ]
    entries: list[dict] = []
    section_checks: list[dict] = []
    for section in sections:
        blocks = _reference_blocks(doc, section)
        numbered = any(_REFERENCE_LABEL.match(block.text) for block in blocks[:5])
        section_entries: list[dict] = []
        for block in blocks:
            normalized_text = " ".join(block.text.split())
            label = _REFERENCE_LABEL.match(normalized_text) if numbered else None
            if numbered and label is None and section_entries:
                entry = section_entries[-1]
                entry["text"] = f"{entry['text']} {normalized_text}"
                entry["block_ids"].append(block.id)
                if block.page not in entry["pages"]:
                    entry["pages"].append(block.page)
                entry["dois"] = _dois(entry["text"])
                entry["verification"]["evidence"].append({
                    "source": "source_text",
                    "block_id": block.id,
                })
                continue
            text = normalized_text
            ordinal = int(label.group(1)) if label else None
            if label:
                text = text[label.end():].strip()
            section_entries.append({
                "ordinal": ordinal,
                "text": text,
                "dois": _dois(text),
                "section_id": section.id,
                "block_ids": [block.id],
                "pages": [block.page],
                "verification": {
                    "status": "single_source",
                    "evidence": [{"source": "source_text", "block_id": block.id}],
                },
            })

        ordinals = [entry["ordinal"] for entry in section_entries if entry["ordinal"] is not None]
        expected = list(range(ordinals[0], ordinals[-1] + 1)) if ordinals else []
        section_checks.append({
            "section_id": section.id,
            "count": len(section_entries),
            "numbering": {
                "status": (
                    "sequence_complete" if ordinals and ordinals == expected
                    else "sequence_gaps" if ordinals
                    else "unnumbered"
                ),
                "observed": ordinals,
                "missing": sorted(set(expected) - set(ordinals)),
            },
        })
        entries.extend(section_entries)
    return entries, section_checks


def _reference_similarity(left: str, right: str) -> float:
    normalize = lambda value: re.sub(r"\W+", " ", value.casefold()).strip()
    return SequenceMatcher(None, normalize(left), normalize(right)).ratio()


def _merge_grobid_references(local: list[dict], grobid: list[dict] | None) -> list[dict]:
    if not grobid:
        return local
    by_ordinal = {entry["ordinal"]: entry for entry in local if entry["ordinal"] is not None}
    for parsed in grobid:
        ordinal = parsed.get("index")
        local_entry = by_ordinal.get(ordinal)
        if local_entry is None:
            local.append({
                "ordinal": ordinal,
                "text": parsed.get("text", ""),
                "dois": [parsed["doi"]] if parsed.get("doi") else [],
                "section_id": None,
                "block_ids": [],
                "pages": [],
                "verification": {
                    "status": "single_source",
                    "evidence": [{"source": "grobid_reference"}],
                },
            })
            continue
        grobid_doi = parsed.get("doi")
        local_dois = local_entry["dois"]
        agrees = (
            bool(grobid_doi and grobid_doi in local_dois)
            or _reference_similarity(local_entry["text"], parsed.get("text", "")) >= 0.72
        )
        conflict = bool(grobid_doi and local_dois and grobid_doi not in local_dois)
        local_entry["verification"]["status"] = (
            "conflict" if conflict else "corroborated" if agrees else "single_source"
        )
        local_entry["verification"]["evidence"].append({
            "source": "grobid_reference",
            "text": parsed.get("text", ""),
            **({"doi": grobid_doi} if grobid_doi else {}),
        })
    return sorted(local, key=lambda item: (
        item["ordinal"] is None,
        item["ordinal"] if item["ordinal"] is not None else 0,
    ))


def _section_pages(section: Section, doc: Document) -> dict[str, int]:
    block_ids = _section_block_ids(section)
    pages = [block.page for block in doc.blocks if block.id in block_ids]
    return {
        "start": min(pages, default=section.page_start),
        "end": max(pages, default=section.page_start),
    }


def build_document_metadata(
    doc: Document,
    meta: dict,
    *,
    section_source: str,
    grobid_references: list[dict] | None = None,
) -> dict:
    title = meta.get("title") or Path(doc.source_path).stem
    provisional_roles = semantic_roles(
        doc.sections,
        document_title=title,
        document_kind="unknown",
    )
    kind = _kind_record(doc, meta, section_source, provisional_roles)
    roles = semantic_roles(
        doc.sections,
        document_title=title,
        document_kind=kind["value"],
    )
    kind = _kind_record(doc, meta, section_source, roles)

    local_references, reference_sections = _local_references(doc, roles)
    references = _merge_grobid_references(local_references, grobid_references)
    fields = {field: _field_verification(field, meta) for field in _FIELD_NAMES}
    statuses = Counter(
        record["verification"]["status"] for record in fields.values()
    )
    statuses.update(
        entry["verification"]["status"] for entry in references
    )
    statuses[kind["verification"]["status"]] += 1

    return {
        "schema_version": METADATA_SCHEMA_VERSION,
        "document": {
            "id": doc.doc_id,
            "source": "../source.pdf",
            "pages": doc.page_count,
            "kind": kind,
            "fields": fields,
        },
        "sections": [
            {
                "section_id": section.id,
                "title": section.title,
                "kind": section.kind.value,
                "semantic_role": roles[section.id]["role"],
                "pages": _section_pages(section, doc),
                "verification": roles[section.id]["verification"],
            }
            for section in _sections(doc.sections)
        ],
        "references": {
            "count": len(references),
            "sections": reference_sections,
            "items": references,
            "structured_source": "data/grobid-references.tei.xml" if grobid_references else None,
        },
        "external_sources": ([{
            "type": "doi_registry_csl",
            "path": meta["doi_registry_path"],
            "doi": meta.get("doi"),
        }] if meta.get("doi_registry_path") else []),
        "verification_summary": dict(sorted(statuses.items())),
    }


def write_document_metadata(version_dir: Path, metadata: dict) -> Path:
    path = Path(version_dir) / METADATA_NAME
    path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n")
    return path
