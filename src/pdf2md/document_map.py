"""Build the compact document map used for navigation and retrieval planning.

The map derives from the section tree and emitted passages, so it does not create
a second interpretation of reading order or source provenance.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from pdf2md.schema import Document, Section


OUTLINE_SCHEMA_VERSION = 2
_BIBLIOGRAPHY = re.compile(
    r"\b(bibliograph|references|works cited|further reading|recommended reading)\b",
    re.IGNORECASE,
)
_GLOSSARY = re.compile(
    r"\b(glossar|nomenclature|notation(?:al)?|symbols|abbreviations)\b",
    re.IGNORECASE,
)
_INDEX = re.compile(r"\bindex\b", re.IGNORECASE)


def _sections(root: Section) -> list[Section]:
    ordered = []

    def walk(section: Section) -> None:
        ordered.append(section)
        for child in section.children:
            walk(child)

    walk(root)
    return ordered


def _ranges(indices: list[int], passages: list[dict]) -> list[dict]:
    if not indices:
        return []
    groups: list[list[int]] = [[indices[0]]]
    for index in indices[1:]:
        if index == groups[-1][-1] + 1:
            groups[-1].append(index)
        else:
            groups.append([index])
    return [{
        "start_index": group[0],
        "end_index": group[-1],
        "start_id": passages[group[0]]["id"],
        "end_id": passages[group[-1]]["id"],
        "count": len(group),
    } for group in groups]


def _counts(passages: list[dict], indices: list[int]) -> dict[str, int]:
    counts = Counter(
        content_type
        for index in indices
        for content_type in passages[index]["content_types"]
    )
    return dict(sorted(counts.items()))


def _block_counts(block_ids: set[str], blocks: dict) -> dict[str, int]:
    counts = Counter(
        blocks[block_id].type.value for block_id in block_ids if block_id in blocks
    )
    return dict(sorted(counts.items()))


def _section_block_ids(section: Section) -> set[str]:
    return {
        *section.block_ids,
        *(
            block_id
            for child in section.children
            for block_id in _section_block_ids(child)
        ),
    }


def _hotspots(passages: list[dict], indices: list[int]) -> list[dict]:
    by_page: dict[int, Counter] = {}
    seen = set()
    for index in indices:
        passage = passages[index]
        dispositions = passage["review"]["dispositions"]
        if not dispositions:
            continue
        pages = {source["page"] for source in passage["sources"]}
        primary = next(
            (source["block_id"] for source in passage["sources"] if source["role"] == "primary"),
            passage["id"],
        )
        for page in pages:
            counts = by_page.setdefault(page, Counter())
            for disposition in dispositions:
                key = (page, disposition, primary)
                if key not in seen:
                    counts[disposition] += 1
                    seen.add(key)
    return [
        {
            "page": page,
            "action_required": counts["action_required"],
            "source_dependent": counts["source_dependent"],
            "informational": counts["informational"],
            "total": sum(counts.values()),
        }
        for page, counts in sorted(by_page.items())
    ]


def _page_end(section: Section, ordered: list[Section], pages: list[int], total: int) -> int:
    if pages:
        return max(section.page_start, max(pages))
    position = ordered.index(section)
    for candidate in ordered[position + 1:]:
        if candidate.depth <= section.depth and candidate.page_start > section.page_start:
            return max(section.page_start, candidate.page_start - 1)
    return total


def build_document_map(
    doc: Document,
    meta: dict,
    md_files: list[Path],
    passages: list[dict],
    *,
    section_roles: dict[str, str] | None = None,
) -> dict:
    section_roles = section_roles or {}
    ordered_sections = _sections(doc.sections)
    blocks = {block.id: block for block in doc.blocks}
    section_passages: dict[str, list[int]] = {section.id: [] for section in ordered_sections}
    for index, passage in enumerate(passages):
        for breadcrumb in passage["section_breadcrumb"]:
            if breadcrumb["id"] in section_passages:
                section_passages[breadcrumb["id"]].append(index)

    file_entries = []
    for path in md_files:
        indices = [
            index for index, passage in enumerate(passages)
            if passage["markdown"] == path.name
        ]
        pages = sorted({
            source["page"]
            for index in indices
            for source in passages[index]["sources"]
        })
        block_ids = {
            source["block_id"]
            for index in indices
            for source in passages[index]["sources"]
            if source["role"] == "primary"
        }
        file_entries.append({
            "path": path.name,
            "pages": {
                "start": pages[0] if pages else None,
                "end": pages[-1] if pages else None,
            },
            "passage_ranges": _ranges(indices, passages),
            "passage_count": len(indices),
            "block_counts_by_content_type": _block_counts(block_ids, blocks),
            "passage_counts_by_content_type": _counts(passages, indices),
            "review_hotspots": _hotspots(passages, indices),
        })

    def node(section: Section) -> dict:
        indices = section_passages[section.id]
        pages = sorted({
            source["page"]
            for index in indices
            for source in passages[index]["sources"]
        })
        markdown = list(dict.fromkeys(
            passages[index]["markdown"]
            for index in indices
            if passages[index]["markdown"]
        ))
        end = _page_end(section, ordered_sections, pages, doc.page_count)
        if not markdown:
            candidates = [
                entry["path"] for entry in file_entries
                if entry["pages"]["start"] is not None
                and entry["pages"]["start"] <= section.page_start <= entry["pages"]["end"]
            ]
            markdown = candidates[:1] or (
                [file_entries[0]["path"]] if file_entries else []
            )
        return {
            "id": section.id,
            "title": section.title,
            "depth": section.depth,
            "kind": section.kind.value,
            "semantic_role": section_roles.get(section.id, "body"),
            "pages": {"start": section.page_start, "end": end},
            "markdown": markdown,
            "passage_ranges": _ranges(indices, passages),
            "passage_count": len(indices),
            "block_counts_by_content_type": _block_counts(
                _section_block_ids(section), blocks
            ),
            "passage_counts_by_content_type": _counts(passages, indices),
            "review_hotspots": _hotspots(passages, indices),
            "children": [node(child) for child in section.children],
        }

    locations = []
    for section in ordered_sections:
        kind = (
            "bibliography" if _BIBLIOGRAPHY.search(section.title)
            else "glossary" if _GLOSSARY.search(section.title)
            else "index" if _INDEX.search(section.title)
            else None
        )
        if kind:
            mapped = node(section)
            locations.append({
                "type": kind,
                "section_id": section.id,
                "title": section.title,
                "pages": mapped["pages"],
                "markdown": mapped["markdown"],
            })

    semantic_locations = []
    for section in ordered_sections:
        role = section_roles.get(section.id, "body")
        if role in {"body", "document"}:
            continue
        mapped = node(section)
        semantic_locations.append({
            "role": role,
            "section_id": section.id,
            "title": section.title,
            "pages": mapped["pages"],
            "markdown": mapped["markdown"],
            "passage_ranges": mapped["passage_ranges"],
        })

    source_dependent = []
    for passage in passages:
        if (
            passage["authority"] != "source_image"
            and "source_dependent" not in passage["review"]["dispositions"]
        ):
            continue
        source_dependent.append({
            "passage_id": passage["id"],
            "markdown": passage["markdown"],
            "content_types": passage["content_types"],
            "sources": passage["sources"],
            "assets": passage["assets"],
        })

    all_indices = list(range(len(passages)))
    return {
        "schema_version": OUTLINE_SCHEMA_VERSION,
        "document": {
            "id": doc.doc_id,
            "title": meta.get("title") or Path(doc.source_path).stem,
            "pages": doc.page_count,
        },
        "section_count": len(ordered_sections),
        "source": {"path": "../source.pdf", "sha256": doc.source_sha256},
        "markdown_files": file_entries,
        "outline": node(doc.sections),
        "block_counts_by_content_type": _block_counts(set(blocks), blocks),
        "passage_counts_by_content_type": _counts(passages, all_indices),
        "review_hotspots": _hotspots(passages, all_indices),
        "named_locations": locations,
        "semantic_locations": semantic_locations,
        "source_dependent_regions": source_dependent,
    }


def write_document_map(
    version_dir: Path,
    doc: Document,
    meta: dict,
    md_files: list[Path],
    passages_path: Path,
    *,
    section_roles: dict[str, str] | None = None,
) -> Path:
    passages = [
        json.loads(line)
        for line in passages_path.read_text().splitlines()
        if line.strip()
    ]
    path = version_dir / "outline.json"
    path.write_text(json.dumps(build_document_map(
        doc,
        meta,
        md_files,
        passages,
        section_roles=section_roles,
    ), indent=2) + "\n")
    return path
