"""Build the logical-section tree that determines the output file layout.

Source of truth: embedded bookmarks when present (the book/chapter case → split
into per-chapter files), else the detected heading outline (the paper case →
single file), else nothing usable (→ single `document.md`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pdf2md.outline import heading_depth, is_label_heading, section_kind
from pdf2md.schema import Block, BlockType, Section, SectionKind


# Below this page count a document is treated as a paper (single file) even when
# it ships bookmarks; at or above it, bookmarked top-level sections become files.
SPLIT_MIN_PAGES = 40
_ROMAN_PART = re.compile(r"^[ivxlcdm]+\s*[:.]\s+\S", re.IGNORECASE)
_NUMBERED_CHAPTER = re.compile(r"^\d+\s+\S")
_EXPLICIT_CHAPTER = re.compile(
    r"^chapter\s+(?:\d+|[ivxlcdm]+)\b(?:\s*[:.]?\s*\S.*)?$",
    re.IGNORECASE,
)
_APPENDIX_CHILD = re.compile(r"^appendix\s+[a-z0-9]+\b", re.IGNORECASE)


@dataclass
class StructureResult:
    root: Section
    section_source: str  # "bookmarks" | "heading_outline" | "none"
    split: bool          # emit a navigable book bundle vs a single file
    split_depth: int = 0 # deepest preferred boundary; selective rules avoid local headings


def build_structure(
    blocks: list[Block], bookmarks, *, title: str, page_count: int
) -> StructureResult:
    if bookmarks:
        return _from_bookmarks(blocks, bookmarks, title, page_count)
    return _from_headings(blocks, title)


def _new_root(title: str, blocks: list[Block]) -> Section:
    return Section(
        id="root",
        title=title,
        depth=0,
        kind=SectionKind.SECTION,
        page_start=blocks[0].page if blocks else 1,
    )


def _from_headings(blocks: list[Block], title: str) -> StructureResult:
    root = _new_root(title, blocks)
    stack = [root]
    has_heading = False
    for b in blocks:
        if b.type == BlockType.HEADING and b.text.strip():
            has_heading = True
            depth = heading_depth(b)
            node = Section(
                id=b.id,
                title=b.text.strip(),
                depth=depth,
                kind=section_kind(b.text, depth),
                page_start=b.page,
                block_ids=[b.id],
            )
            while len(stack) > 1 and stack[-1].depth >= depth:
                stack.pop()
            stack[-1].children.append(node)
            stack.append(node)
        else:
            stack[-1].block_ids.append(b.id)
    source = "heading_outline" if has_heading else "none"
    return StructureResult(root, source, split=False)


def is_chapter_container(section: Section) -> bool:
    """Whether direct children are useful file boundaries rather than local headings."""
    title = section.title.strip()
    if section.kind is SectionKind.PART or title.casefold().startswith("part "):
        return True
    if _ROMAN_PART.match(title):
        return any(_NUMBERED_CHAPTER.match(child.title.strip()) for child in section.children)
    if title.casefold() == "appendices":
        return any(_APPENDIX_CHILD.match(child.title.strip()) for child in section.children)
    return False


def _book_split_depth(root: Section) -> int:
    if any(is_chapter_container(section) and section.children for section in root.children):
        return 2
    return 1


def _heading_chapters(section: Section, blocks: list[Block]) -> None:
    """Add chapter children when a Part bookmark has only heading-level detail."""
    if section.children or not is_chapter_container(section):
        return
    by_id = {block.id: block for block in blocks}
    owned = [by_id[block_id] for block_id in section.block_ids if block_id in by_id]
    candidates = [
        (index, block)
        for index, block in enumerate(owned)
        if block.type is BlockType.HEADING
        and (
            _EXPLICIT_CHAPTER.match(block.text.strip())
            or _NUMBERED_CHAPTER.match(block.text.strip())
        )
    ]
    if len(candidates) < 2:
        return

    section.kind = SectionKind.PART
    section.block_ids = [block.id for block in owned[:candidates[0][0]]]
    for position, (start, heading) in enumerate(candidates):
        end = candidates[position + 1][0] if position + 1 < len(candidates) else len(owned)
        section.children.append(Section(
            id=heading.id,
            title=heading.text.strip(),
            depth=section.depth + 1,
            kind=SectionKind.CHAPTER,
            page_start=heading.page,
            block_ids=[block.id for block in owned[start:end]],
        ))


def _from_bookmarks(
    blocks: list[Block], bookmarks, title: str, page_count: int
) -> StructureResult:
    root = _new_root(title, blocks)
    stack = [root]
    ordered: list[Section] = []
    ordered_bookmarks = sorted(
        enumerate(bookmarks),
        key=lambda item: (item[1][1], item[0]),
    )
    for _, (btitle, page_index, level) in ordered_bookmarks:
        depth = level + 1
        kind = section_kind(btitle, depth)
        if depth == 1 and kind is SectionKind.SECTION:
            kind = SectionKind.CHAPTER
        node = Section(
            id=f"bm:{page_index}:{btitle[:24]}",
            title=btitle,
            depth=depth,
            kind=kind,
            page_start=page_index + 1,
        )
        while len(stack) > 1 and stack[-1].depth >= depth:
            stack.pop()
        stack[-1].children.append(node)
        stack.append(node)
        ordered.append(node)

    # Assign each block to the last section that starts on or before its page.
    for b in blocks:
        target = root
        for s in ordered:
            if s.page_start <= b.page:
                target = s
            else:
                break
        target.block_ids.append(b.id)

    part_pages = {
        block.page
        for block in blocks
        if block.type is BlockType.HEADING
        and block.text.strip().casefold().startswith("part ")
        and is_label_heading(block.text)
    }
    for section in root.children:
        if section.page_start in part_pages:
            section.kind = SectionKind.PART

    for section in root.children:
        _heading_chapters(section, blocks)

    has_multiple_units = (
        len(root.children) >= 2
        or any(
            is_chapter_container(section) and len(section.children) >= 2
            for section in root.children
        )
    )
    split = has_multiple_units and page_count >= SPLIT_MIN_PAGES
    split_depth = _book_split_depth(root) if split else 0
    return StructureResult(root, "bookmarks", split=split, split_depth=split_depth)
