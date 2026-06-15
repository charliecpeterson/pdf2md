"""Build the logical-section tree that determines the output file layout.

Source of truth: embedded bookmarks when present (the book/chapter case → split
into per-chapter files), else the detected heading outline (the paper case →
single file), else nothing usable (→ single `document.md`).
"""

from __future__ import annotations

from dataclasses import dataclass

from pdf2md.outline import heading_depth, section_kind
from pdf2md.schema import Block, BlockType, Section, SectionKind


# Below this page count a document is treated as a paper (single file) even when
# it ships bookmarks; at or above it, bookmarked top-level sections become files.
SPLIT_MIN_PAGES = 40


@dataclass
class StructureResult:
    root: Section
    section_source: str  # "bookmarks" | "heading_outline" | "none"
    split: bool          # emit one file per top-level section vs a single file


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


def _from_bookmarks(
    blocks: list[Block], bookmarks, title: str, page_count: int
) -> StructureResult:
    root = _new_root(title, blocks)
    stack = [root]
    ordered: list[Section] = []
    for btitle, page_index, level in bookmarks:
        depth = level + 1
        node = Section(
            id=f"bm:{page_index}:{btitle[:24]}",
            title=btitle,
            depth=depth,
            kind=SectionKind.CHAPTER if depth == 1 else SectionKind.SECTION,
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

    split = len(root.children) >= 2 and page_count >= SPLIT_MIN_PAGES
    return StructureResult(root, "bookmarks", split=split)
