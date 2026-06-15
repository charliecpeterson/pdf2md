"""Serialize a Document to logical-section markdown files with YAML front-matter.

Walks blocks in reading order, sets each block's coverage_status as it renders,
and collects a visible marker for anything it can't represent (the lossless
invariant). Papers emit one `document.md`; books (bookmarks) split per chapter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from pdf2md.outline import heading_depth
from pdf2md.schema import (
    FORMAT_VERSION,
    Block,
    BlockType,
    CoverageFlag,
    CoverageStatus,
    Document,
    FigureRef,
    Section,
    TableData,
)
from pdf2md.tables import render_table

_BOILERPLATE = {BlockType.PAGE_HEADER, BlockType.PAGE_FOOTER}


@dataclass
class _Ctx:
    depth_of: dict[str, int]
    tables: dict[str, TableData]
    figures: dict[str, FigureRef]
    footnotes: list[str] = field(default_factory=list)
    flags: list[CoverageFlag] = field(default_factory=list)


def emit_document(
    doc: Document, structure, version_dir: Path, meta: dict, engine_versions: dict
) -> tuple[list[Path], list[CoverageFlag]]:
    version_dir.mkdir(parents=True, exist_ok=True)
    by_id = {b.id: b for b in doc.blocks}
    ctx = _Ctx(
        depth_of=_depth_map(structure.root),
        tables={t.block_id: t for t in doc.tables},
        figures={f.block_id: f for f in doc.figures},
    )
    base_front = _front_matter(doc, meta, structure.section_source, engine_versions)

    written: list[Path] = []
    if structure.split:
        front_ids = list(structure.root.block_ids)
        if front_ids:
            written.append(
                _write(version_dir / "00_front.md", base_front, "Front matter",
                       _ordered(doc.blocks, set(front_ids)), ctx)
            )
        for i, section in enumerate(structure.root.children, start=1):
            ids = _subtree_ids(section)
            name = f"{i:02d}_{_slug(section.title)}.md"
            written.append(
                _write(version_dir / name, base_front, section.title,
                       _ordered(doc.blocks, ids), ctx)
            )
    else:
        written.append(
            _write(version_dir / "document.md", base_front,
                   meta.get("title") or "Document", doc.blocks, ctx)
        )

    # Anything never touched by a file (shouldn't happen) is an honest drop.
    for b in doc.blocks:
        if b.coverage_status == CoverageStatus.PENDING:
            b.coverage_status = CoverageStatus.DROPPED
            ctx.flags.append(CoverageFlag(b.id, b.page, "unplaced block", ""))
    return written, ctx.flags


def _write(path: Path, base_front: dict, title: str, blocks: list[Block], ctx: _Ctx) -> Path:
    front = {**base_front, "section_title": title}
    body = _render_blocks(blocks, ctx)
    fm = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
    path.write_text(f"---\n{fm}\n---\n\n# {title}\n\n{body}\n")
    return path


def _render_blocks(blocks: list[Block], ctx: _Ctx) -> str:
    parts: list[str] = []
    footnotes: list[str] = []
    prev_page: int | None = None
    for b in blocks:
        if b.page != prev_page:
            parts.append(f"<!-- page {b.page} -->")
            prev_page = b.page
        text, status, flag = _render_block(b, ctx, footnotes)
        b.coverage_status = status
        if flag is not None:
            ctx.flags.append(flag)
        if text:
            parts.append(text)
    if footnotes:
        parts.append("---")
        parts.extend(f"[^fn{i}]: {fn}" for i, fn in enumerate(footnotes, start=1))
    return "\n\n".join(parts)


def _render_block(
    b: Block, ctx: _Ctx, footnotes: list[str]
) -> tuple[str | None, CoverageStatus, CoverageFlag | None]:
    txt = b.text.strip()

    if b.type in _BOILERPLATE:  # intentionally stripped, not lost
        return None, CoverageStatus.EMITTED, None

    if b.type == BlockType.FIGURE:
        fig = ctx.figures.get(b.id)
        if fig and fig.asset_path:
            alt = _clean_alt(fig.caption or "figure")
            return f"![{alt}]({fig.asset_path})", CoverageStatus.CROPPED, None
        return _marker(b, "figure crop missing"), CoverageStatus.FLAGGED, _flag(b, "figure crop missing")

    if b.type == BlockType.TABLE:
        table = ctx.tables.get(b.id)
        if table:
            return render_table(table), CoverageStatus.EMITTED, None
        return _marker(b, "table not extracted"), CoverageStatus.FLAGGED, _flag(b, "table not extracted")

    if b.type == BlockType.FOOTNOTE:
        if txt:
            footnotes.append(txt)
        return None, CoverageStatus.EMITTED, None

    if not txt:
        return _marker(b, f"empty {b.type.value} block"), CoverageStatus.DROPPED, _flag(b, "empty block")

    if b.type == BlockType.HEADING:
        depth = ctx.depth_of.get(b.id) or heading_depth(b)
        hashes = "#" * max(1, min(depth, 6))
        return f"{hashes} {txt}", CoverageStatus.EMITTED, None
    if b.type == BlockType.LIST:
        return f"- {txt}", CoverageStatus.EMITTED, None
    if b.type == BlockType.CAPTION:
        return f"*{txt}*", CoverageStatus.EMITTED, None
    if b.type == BlockType.CODE:
        return f"```\n{b.text}\n```", CoverageStatus.EMITTED, None
    if b.type == BlockType.EQUATION:
        return f"$$\n{txt.strip('$').strip()}\n$$", CoverageStatus.EMITTED, None
    return txt, CoverageStatus.EMITTED, None


def _marker(b: Block, reason: str) -> str:
    return f"> **[pdf2md: {reason}]** page {b.page}, block `{b.id}`"


def _flag(b: Block, reason: str) -> CoverageFlag:
    return CoverageFlag(b.id, b.page, reason, _marker(b, reason))


def _front_matter(doc: Document, meta: dict, section_source: str, engine_versions: dict) -> dict:
    return {
        "format_version": FORMAT_VERSION,
        "title": meta.get("title"),
        "authors": meta.get("authors"),
        "year": meta.get("year"),
        "doi": meta.get("doi"),
        "source": Path(doc.source_path).name,
        "doc_id": doc.doc_id[:16],
        "pages": doc.page_count,
        "section_source": section_source,
        "engine": engine_versions,
    }


def _depth_map(root: Section) -> dict[str, int]:
    out: dict[str, int] = {}

    def walk(s: Section) -> None:
        out[s.id] = s.depth
        for child in s.children:
            walk(child)

    walk(root)
    return out


def _subtree_ids(section: Section) -> set[str]:
    ids = set(section.block_ids)
    for child in section.children:
        ids |= _subtree_ids(child)
    return ids


def _ordered(blocks: list[Block], ids: set[str]) -> list[Block]:
    return [b for b in blocks if b.id in ids]


def _clean_alt(s: str) -> str:
    return re.sub(r"\s+", " ", s).replace("[", "(").replace("]", ")").strip()


def _slug(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s.lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s)
    return (s[:50] or "section").strip("-")
