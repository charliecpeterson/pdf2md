"""Write token-bounded retrieval passages with stable source provenance.

Passage identity stays anchored to one primary block. Related captions and prose
can add context without weakening source-region or review provenance.
"""

from __future__ import annotations

import hashlib
import json
import re
from importlib.resources import files
from pathlib import Path

from pdf2md.chunks import block_content, section_map
from pdf2md.passage_split import prose_units, split_passage_text
from pdf2md.passage_tokenizer import (
    DEFAULT_TOKENIZER,
    PassageTokenizer,
    load_passage_tokenizer,
)
from pdf2md.schema import (
    BBox,
    Block,
    BlockType,
    CoverageStatus,
    Document,
    FigureRef,
    Section,
    TableData,
)

PASSAGES_SCHEMA_VERSION = 2
DEFAULT_MAX_TOKENS = 512
_FURNITURE = {BlockType.PAGE_HEADER, BlockType.PAGE_FOOTER}
_FIGURE_LABEL = re.compile(
    r"\bfig(?:ure)?\.?\s*([A-Za-z]?\d+(?:[.\-]\d+)*(?:[a-z])?)",
    re.IGNORECASE,
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _passage_id(source_name: str, block_id: str, split_index: int) -> str:
    stable_key = f"{source_name.casefold()}\0{block_id}\0{split_index}"
    return f"passage-{_sha256(stable_key)[:20]}"


def _breadcrumbs(root: Section) -> dict[str, list[dict]]:
    mapping: dict[str, list[dict]] = {}

    def walk(section: Section, parents: list[dict]) -> None:
        current = {
            "id": section.id,
            "title": section.title,
            "depth": section.depth,
            "kind": section.kind.value,
        }
        path = [*parents, current]
        for block_id in section.block_ids:
            mapping[block_id] = path
        for child in section.children:
            walk(child, path)

    walk(root, [])
    return mapping


def _bbox(bbox: BBox | None) -> dict[str, float] | None:
    if bbox is None:
        return None
    return {
        "x0": bbox.x0,
        "y0": bbox.y0,
        "x1": bbox.x1,
        "y1": bbox.y1,
    }


def _authority(block: Block) -> str:
    if (
        block.type is BlockType.FIGURE
        or block.extra.get("crop_path")
        or block.extra.get("ocr")
        or block.extra.get("cells_unverified")
    ):
        return "source_image"
    return "text"


def _nearby_block(
    blocks: list[Block],
    index: int,
    types: set[BlockType],
    *,
    distance: int,
) -> Block | None:
    for offset in range(1, distance + 1):
        for candidate_index in (index - offset, index + offset):
            if not 0 <= candidate_index < len(blocks):
                continue
            candidate = blocks[candidate_index]
            if (
                candidate.type in types
                and abs(candidate.page - blocks[index].page) <= 1
                and candidate.text.strip()
            ):
                return candidate
    return None


def _figure_reference(
    figure: FigureRef | None, blocks: list[Block], index: int
) -> Block | None:
    if figure is None or not figure.caption:
        return None
    label = _FIGURE_LABEL.search(figure.caption)
    if label is None:
        return None
    reference = re.compile(
        rf"\bfig(?:ure)?\.?\s*{re.escape(label.group(1))}\b",
        re.IGNORECASE,
    )
    for offset in range(1, 6):
        for candidate_index in (index - offset, index + offset):
            if not 0 <= candidate_index < len(blocks):
                continue
            candidate = blocks[candidate_index]
            if (
                candidate.type is BlockType.PARAGRAPH
                and abs(candidate.page - blocks[index].page) <= 1
                and reference.search(candidate.text)
            ):
                return candidate
    return None


def _related_context(
    blocks: list[Block],
    index: int,
    figures: dict[str, FigureRef],
) -> tuple[str, list[Block]]:
    block = blocks[index]
    related: Block | None = None
    relation = ""
    excerpt = ""
    if block.type is BlockType.TABLE:
        related = _nearby_block(blocks, index, {BlockType.CAPTION}, distance=1)
        relation = "Table caption"
        excerpt = related.text.strip() if related else ""
    elif block.type is BlockType.EQUATION:
        related = _nearby_block(blocks, index, {BlockType.PARAGRAPH}, distance=2)
        relation = "Explanatory context"
        if related:
            sentences = prose_units(related.text)
            excerpt = (
                sentences[-1]
                if any(candidate is related for candidate in blocks[:index])
                else sentences[0]
            )
    elif block.type is BlockType.FIGURE:
        figure = figures.get(block.id)
        related = _figure_reference(figure, blocks, index)
        relation = "Referring text"
        if related and figure:
            reference = _FIGURE_LABEL.search(figure.caption or "")
            sentences = prose_units(related.text)
            excerpt = next(
                (
                    sentence for sentence in sentences
                    if reference and re.search(
                        rf"\bfig(?:ure)?\.?\s*{re.escape(reference.group(1))}\b",
                        sentence,
                        re.IGNORECASE,
                    )
                ),
                related.text.strip(),
            )
    if not related or not excerpt:
        return "", []
    return f"{relation}: {excerpt}", [related]


def _source_records(
    block: Block,
    related_blocks: list[Block],
    figure: FigureRef | None,
) -> list[dict]:
    records = [{
        "block_id": block.id,
        "page": block.page,
        "bbox": _bbox(block.bbox),
        "source_page": f"../source.pdf#page={block.page}",
        "role": "primary",
    }]
    if figure and figure.caption and figure.caption_bbox:
        records.append({
            "block_id": block.id,
            "page": block.page,
            "bbox": _bbox(figure.caption_bbox),
            "source_page": f"../source.pdf#page={block.page}",
            "role": "caption",
        })
    records.extend(
        {
            "block_id": related.id,
            "page": related.page,
            "bbox": _bbox(related.bbox),
            "source_page": f"../source.pdf#page={related.page}",
            "role": "caption" if related.type is BlockType.CAPTION else "context",
        }
        for related in related_blocks
    )
    return records


def _asset(path: str, asset_type: str, provenance: str) -> dict | None:
    if not path:
        return None
    return {"path": path, "type": asset_type, "provenance": provenance}


def _assets(
    block: Block,
    table: TableData | None,
    figure: FigureRef | None,
    page_rasters: dict[int, str],
) -> list[dict]:
    records = []
    crop_type = "source_crop"
    if block.type is BlockType.FIGURE:
        crop_type = "figure_image"
    records.append(_asset(block.extra.get("crop_path", ""), crop_type, "source_render"))
    if block.extra.get("ocr"):
        records.append(_asset(
            page_rasters.get(block.page, ""), "source_page_image", "source_render"
        ))

    if table is not None:
        records.extend([
            _asset(table.candidate_path, "table_candidate", "engine_derived"),
            _asset(table.data_path, "table_csv", "engine_derived"),
            _asset(table.json_path, "table_json", "engine_derived"),
            _asset(
                table.normalized_data_path,
                "table_normalized_csv",
                "deterministic_derived",
            ),
            _asset(
                table.normalized_json_path,
                "table_normalized_json",
                "deterministic_derived",
            ),
            _asset(
                table.cell_evidence_path,
                "table_cell_evidence",
                "deterministic_derived",
            ),
        ])

    if figure is not None:
        digitization_provenance = (
            "model_derived"
            if figure.digitization and "vlm" in figure.digitization.method
            else "deterministic_derived"
        )
        records.extend([
            _asset(figure.asset_path, "figure_image", "source_render"),
            _asset(figure.svg_path, "figure_svg", "source_render"),
            _asset(figure.data_path, "chart_data", digitization_provenance),
            _asset(figure.code_path, "reproduction_code", "deterministic_derived"),
        ])

    unique = {}
    for record in records:
        if record is not None:
            unique.setdefault(record["path"], record)
    return list(unique.values())


def _retrieval_text(
    meta: dict,
    breadcrumb: list[dict],
    content_type: str,
    text: str,
    *,
    semantic_role: str | None = None,
) -> str:
    context = [f"Document: {meta['title']}"]
    if meta["authors"]:
        context.append("Authors: " + "; ".join(meta["authors"]))
    titles = []
    for section in breadcrumb:
        if section["title"] and section["title"] not in titles:
            titles.append(section["title"])
    if titles:
        context.append("Section: " + " > ".join(titles))
    if semantic_role and semantic_role not in {"body", "document"}:
        context.append("Semantic role: " + semantic_role)
    context.append(f"Content type: {content_type}")
    return "\n".join([*context, "", text])


def load_passage_schema() -> dict:
    schema_path = files("pdf2md").joinpath("passages-v2.schema.json")
    return json.loads(schema_path.read_text())


def build_passages(
    doc: Document,
    meta: dict,
    md_files: list[Path],
    page_rasters: dict[int, str],
    *,
    emission_index: dict[str, dict] | None = None,
    tokenizer: PassageTokenizer | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    section_roles: dict[str, str] | None = None,
) -> list[dict]:
    """Build block-stable records; editing one block cannot renumber later passages."""
    tokenizer = tokenizer or load_passage_tokenizer(DEFAULT_TOKENIZER)
    section_roles = section_roles or {}
    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")
    model_max_tokens = getattr(tokenizer, "model_max_tokens", None)
    if model_max_tokens is not None and max_tokens > model_max_tokens:
        raise ValueError(
            f"passage_max_tokens {max_tokens} exceeds tokenizer model_max_length "
            f"{model_max_tokens}"
        )

    metadata = {
        "title": meta.get("title") or Path(doc.source_path).stem,
        "authors": meta.get("authors") or [],
        "language": meta.get("language") or "und",
    }
    source_name = Path(doc.source_path).name
    document = {
        "id": doc.doc_id,
        "key": _sha256(source_name.casefold())[:16],
        **metadata,
    }
    breadcrumb_by_block = _breadcrumbs(doc.sections)
    fallback_section = section_map(doc, md_files)
    tables = {table.block_id: table for table in doc.tables}
    figures = {figure.block_id: figure for figure in doc.figures}
    dispositions: dict[str, set[str]] = {}
    for flag in doc.coverage.flags if doc.coverage else []:
        dispositions.setdefault(flag.block_id, set()).add(flag.disposition)

    passages = []
    for block_index, block in enumerate(doc.blocks):
        if block.type in _FURNITURE:
            continue
        text, _ = block_content(block, tables, figures)
        if not text:
            continue
        related_text, related_blocks = _related_context(
            doc.blocks, block_index, figures
        )
        if related_text:
            text = f"{related_text}\n\n{text}"
        markdown = (emission_index or {}).get(block.id, {}).get("markdown")
        if markdown is None:
            markdown = fallback_section.get(block.id, (None, None, None))[2]
        source_blocks = [block, *related_blocks]
        block_dispositions = sorted(set().union(*(
            dispositions.get(source.id, set()) for source in source_blocks
        )))
        breadcrumb = breadcrumb_by_block.get(block.id, [])
        semantic_role = next((
            section_roles[section["id"]]
            for section in reversed(breadcrumb)
            if section["id"] in section_roles
        ), None)
        def contextualize(part: str) -> str:
            return _retrieval_text(
                metadata,
                breadcrumb,
                block.type.value,
                part,
                semantic_role=semantic_role,
            )

        parts = split_passage_text(
            text, block.type, contextualize, tokenizer, max_tokens
        )
        for split_index, part in enumerate(parts):
            retrieval_text = contextualize(part)
            passage_id = _passage_id(source_name, block.id, split_index)
            passages.append({
                "schema_version": PASSAGES_SCHEMA_VERSION,
                "id": passage_id,
                "content_hash": _sha256(retrieval_text),
                "document": document,
                "section_breadcrumb": breadcrumb,
                "display_text": part,
                "retrieval_text": retrieval_text,
                "content_types": list(dict.fromkeys([
                    block.type.value,
                    *(
                        [BlockType.CAPTION.value]
                        if figures.get(block.id) and figures[block.id].caption
                        else []
                    ),
                    *(source.type.value for source in related_blocks),
                ])),
                "markdown": markdown,
                "sources": _source_records(
                    block, related_blocks, figures.get(block.id)
                ),
                "previous_id": None,
                "next_id": None,
                "authority": _authority(block),
                "review": {
                    "needs_review": (
                        "action_required" in block_dispositions
                        or any(source.coverage_status in {
                            CoverageStatus.FLAGGED, CoverageStatus.DROPPED,
                        } for source in source_blocks)
                    ),
                    "dispositions": block_dispositions,
                },
                "assets": _assets(
                    block,
                    tables.get(block.id),
                    figures.get(block.id),
                    page_rasters,
                ),
                "tokenizer": {
                    "id": tokenizer.id,
                    "count": tokenizer.count(retrieval_text),
                    "limit": max_tokens,
                },
                "split": {"index": split_index, "count": len(parts)},
            })

    for index, passage in enumerate(passages):
        if index:
            passage["previous_id"] = passages[index - 1]["id"]
        if index + 1 < len(passages):
            passage["next_id"] = passages[index + 1]["id"]
    return passages


def write_passages(
    version_dir: Path,
    doc: Document,
    meta: dict,
    md_files: list[Path],
    page_rasters: dict[int, str],
    *,
    emission_index: dict[str, dict] | None = None,
    tokenizer: PassageTokenizer | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    section_roles: dict[str, str] | None = None,
) -> tuple[Path, Path, int]:
    passages = build_passages(
        doc,
        meta,
        md_files,
        page_rasters,
        emission_index=emission_index,
        tokenizer=tokenizer,
        max_tokens=max_tokens,
        section_roles=section_roles,
    )
    passages_path = version_dir / "passages.jsonl"
    passages_path.write_text(
        "".join(json.dumps(passage, ensure_ascii=False) + "\n" for passage in passages)
    )
    schema_path = version_dir / "passages.schema.json"
    schema_path.write_text(json.dumps(load_passage_schema(), indent=2) + "\n")
    return passages_path, schema_path, len(passages)
