"""Serialize a Document to logical-section markdown files with YAML front-matter.

Walks blocks in reading order, sets each block's coverage_status as it renders,
and collects a visible marker for anything it can't represent (the coverage
invariant). Papers emit one `document.md`; books (bookmarks) split per chapter.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from pdf2md.legibility import is_garbage
from pdf2md.logging import Progress
from pdf2md.render_block import (
    _Ctx,
    _OutlineEntry,
    _render_blocks,
    _unfit_text_layer,
)
from pdf2md.schema import (
    FORMAT_VERSION,
    PROSE_TYPES,
    Block,
    BlockType,
    CoverageFlag,
    CoverageStatus,
    Document,
    Section,
)
from pdf2md.structure import is_chapter_container
from pdf2md.table_artifacts import write_table_artifacts

# Strip a leading "Part/Chapter/Appendix" word and/or a standalone number or roman
# numeral so "Part IV: Issues …" and the bookmark title "IV Issues …" compare equal.
# The bare numeral must be followed by whitespace, so an initial like "C. elegans" (a
# period, not a space) keeps its "C" instead of being read as a section numeral. `\b`
# keeps a real word ("Introduction") from losing its leading "I".










def emit_document(
    doc: Document, structure, version_dir: Path, meta: dict, engine_versions: dict,
    page_rasters: dict[int, str] | None = None,
    table_ocr_executable: str | None = None,
    table_reference_path: str | None = None,
    progress: Progress | None = None,
    formula_enrichment_enabled: bool = True,
    emission_index: dict[str, dict] | None = None,
) -> tuple[list[Path], list[CoverageFlag]]:
    version_dir.mkdir(parents=True, exist_ok=True)
    ctx = _Ctx(
        depth_of=_depth_map(structure.root),
        tables={t.block_id: t for t in doc.tables},
        figures={f.block_id: f for f in doc.figures},
        version_dir=version_dir,
        page_rasters=page_rasters or {},
        formula_enrichment_enabled=formula_enrichment_enabled,
        emission_index=emission_index,
    )
    write_table_artifacts(
        doc, version_dir, table_ocr_executable, table_reference_path, progress
    )
    base_front = _front_matter(doc, meta, structure.section_source, engine_versions)

    written: list[Path] = []
    if structure.split:
        outline: list[_OutlineEntry] = []
        front_ids = list(structure.root.block_ids)
        if front_ids:
            path, heads = _write(version_dir / "00_front.md", base_front, "Front matter",
                                 _ordered(doc.blocks, set(front_ids)), ctx, base_depth=1)
            written.append(path)
            outline.append(_OutlineEntry(path.name, "Front matter", heads, 1))
        for i, (section, ids) in enumerate(
            _file_units(structure.root, structure.split_depth), start=1
        ):
            name = f"{i:02d}_{_slug(section.title)}.md"
            # The file title is the section's H1; deepen body headings so chapters
            # and numbered sections nest under it instead of all landing at H1.
            path, heads = _write(version_dir / name, base_front, section.title,
                                 _ordered(doc.blocks, ids), ctx, base_depth=1,
                                 local_contents=True)
            written.append(path)
            outline.append(_OutlineEntry(path.name, section.title, heads, section.depth))
        written.append(_write_index(version_dir, base_front, meta, outline))
    else:
        path, heads = _write(version_dir / "document.md", base_front,
                             meta.get("title") or "Document", doc.blocks, ctx)
        written.append(path)
        outline = [_OutlineEntry(path.name, "", heads, 0)]

    # Turn "see section 9.2" into a link to that heading (in this file or another).
    section_map = _section_map(outline)
    if section_map:
        for p in written:
            if p.name != "index.md":
                _link_refs(p, section_map)

    # Anything never touched by a file (shouldn't happen) is an honest drop.
    for b in doc.blocks:
        if b.coverage_status == CoverageStatus.PENDING:
            b.coverage_status = CoverageStatus.DROPPED
            ctx.flags.append(CoverageFlag(
                b.id,
                b.page,
                "unplaced block",
                "",
                severity="high",
                content_impact="high",
            ))

    verdict = _unfit_text_layer(doc)
    if verdict is not None:
        ctx.flags.append(verdict)
    return written, ctx.flags


def _anchor(text: str) -> str:
    """GitHub-style heading anchor: lowercase, drop punctuation, spaces to hyphens."""
    s = re.sub(r"[^\w\s-]", "", text.strip().lower())
    return re.sub(r"\s+", "-", s)


# A cross-reference to a numbered section: "section 9.2", "Sect. 3.5", "§1.1". The
# number must be dotted, so a bare "section 9" (ambiguous with a chapter) is left
# alone; it is linked only when the number resolves to a real heading.
_SECTION_REF = re.compile(r"\b(?:sections?|sect\.?|§)\s*(\d+(?:\.\d+)+)\b", re.I)


def _section_map(outline: list[_OutlineEntry]) -> dict[str, tuple[str, str]]:
    """number -> (file, anchor) from headings whose text starts with a dotted number."""
    m: dict[str, tuple[str, str]] = {}
    for entry in outline:
        for _level, text, _page in entry.headings:
            mm = re.match(r"^(\d+(?:\.\d+)+)\b", text)
            if mm:
                m.setdefault(mm.group(1), (entry.filename, _anchor(text)))
    return m


def _link_refs(path: Path, section_map: dict[str, tuple[str, str]]) -> None:
    """Linkify numbered-section references in a file's body, skipping front-matter and
    code fences (a console session that mentions 'section 9.2' must stay verbatim)."""
    def repl(m: re.Match) -> str:
        target = section_map.get(m.group(1))
        if target is None:
            return m.group(0)
        fname, anchor = target
        href = f"#{anchor}" if fname == path.name else f"{fname}#{anchor}"
        return f"[{m.group(0)}]({href})"

    out: list[str] = []
    in_fm = fm_done = fenced = False
    for i, line in enumerate(path.read_text().splitlines()):
        if not fm_done:
            if i == 0 and line.strip() == "---":
                in_fm = True
                out.append(line)
                continue
            if in_fm:
                if line.strip() == "---":
                    fm_done = True
                out.append(line)
                continue
        if line.startswith("```"):
            fenced = not fenced
            out.append(line)
            continue
        out.append(line if fenced else _SECTION_REF.sub(repl, line))
    path.write_text("\n".join(out) + "\n")


def _write_index(
    version_dir: Path,
    base_front: dict,
    meta: dict,
    outline: list[_OutlineEntry],
) -> Path:
    """Write a shallow file-level index; each file owns its detailed local contents."""
    title = meta.get("title") or "Document"
    lines = [f"# {title}: Contents", ""]
    for entry in outline:
        indent = "  " * max(0, entry.depth - 1)
        lines.append(f"{indent}- [{entry.title}]({entry.filename})")
    front = {k: v for k, v in {**base_front, "section_title": "Contents"}.items() if v is not None}
    fm = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
    path = version_dir / "index.md"
    path.write_text(f"---\n{fm}\n---\n\n" + "\n".join(lines) + "\n")
    return path


def _local_contents(headings: list[tuple[int, str, int]]) -> str:
    if not headings:
        return ""
    base_level = min(level for level, _text, _page in headings)
    lines = ["## In this file", ""]
    for level, text, _page in headings:
        indent = "  " * max(0, level - base_level)
        lines.append(f"{indent}- [{text}](#{_anchor(text)})")
    return "\n".join(lines) + "\n\n"


def _write(
    path: Path,
    base_front: dict,
    title: str,
    blocks: list[Block],
    ctx: _Ctx,
    *,
    base_depth: int = 0,
    local_contents: bool = False,
) -> tuple[Path, list[tuple[int, str, int]]]:
    # Drop null-valued keys: Quarto's schema rejects `doi: null` / `authors: null`
    # (a field declared as a string can't be null), failing the whole render.
    front = {k: v for k, v in {**base_front, "section_title": title}.items() if v is not None}
    ctx.markdown_path = path.name
    body = _render_blocks(blocks, ctx, title=title, base_depth=base_depth)
    contents = _local_contents(ctx.headings) if local_contents else ""
    fm = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
    prefix = f"---\n{fm}\n---\n\n# {title}\n\n{contents}"
    if ctx.emission_index is not None:
        for emission in ctx.emission_index.values():
            if emission.get("markdown") != path.name or "body_start" not in emission:
                continue
            emission["start"] = len(prefix) + emission.pop("body_start")
            emission["end"] = len(prefix) + emission.pop("body_end")
    path.write_text(f"{prefix}{body}\n")
    return path, list(ctx.headings)






















def _front_matter(doc: Document, meta: dict, section_source: str, engine_versions: dict) -> dict:
    front = {
        "format_version": FORMAT_VERSION,
        "title": meta.get("title"),
        "authors": meta.get("authors"),
        "year": meta.get("year"),
        "doi": meta.get("doi"),
        "document_type": meta.get("document_type"),
        "metadata": meta.get("metadata_artifact"),
        "source": Path(doc.source_path).name,
        "doc_id": doc.doc_id[:16],
        "pages": doc.page_count,
        "section_source": section_source,
        # not "engine": that key is reserved by Quarto's YAML front-matter.
        "engine_versions": engine_versions,
    }
    # GROBID enrichment: the specialized parser's fields plus provenance of who
    # answered. Additive keys only — a naive front-matter parser ignores them.
    for key in (
        "abstract", "keywords", "venue", "publisher", "volume", "issue",
        "pages", "article_number", "citation_locator", "issn", "isbn", "edition",
        "publication_dates", "references_count",
    ):
        if meta.get(key):
            front[key] = meta[key]
    if meta.get("metadata_source"):
        front["metadata_source"] = meta["metadata_source"]
    eqs = [b for b in doc.blocks if b.type == BlockType.EQUATION]
    if eqs:
        image_backed = sum(1 for b in eqs if b.extra.get("crop_path"))
        # "image_backed" = extraction couldn't be verified, so an authoritative
        # crop is attached; the rest render as LaTeX the cross-check agreed with.
        front["equations"] = {"total": len(eqs), "image_backed": image_backed}
    # Pages OCR'd from a scan: the text is a best-effort transcription, not the
    # source of truth — downstream consumers should verify against the images.
    scanned = sorted({b.page for b in doc.blocks if b.extra.get("ocr")})
    if scanned:
        front["ocr_scanned_pages"] = len(scanned)
    # Prose blocks whose text stayed symbol-font garbage (broken font, no pdfium
    # rescue): surfaced so a downstream reader knows the doc is partly unreadable.
    illegible = sum(1 for b in doc.blocks
                    if b.type in PROSE_TYPES and b.text.strip() and is_garbage(b.text))
    if illegible:
        front["illegible_blocks"] = illegible
    return front


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


def _file_units(root: Section, split_depth: int) -> list[tuple[Section, set[str]]]:
    """Expand chapter containers while keeping other top-level branches intact."""
    units: list[tuple[Section, set[str]]] = []
    for section in root.children:
        if split_depth == 2 and is_chapter_container(section) and section.children:
            units.append((section, set(section.block_ids)))
            units.extend((child, _subtree_ids(child)) for child in section.children)
        else:
            units.append((section, _subtree_ids(section)))
    return units


def _ordered(blocks: list[Block], ids: set[str]) -> list[Block]:
    return [b for b in blocks if b.id in ids]


def _slug(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s.lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s)
    return (s[:50] or "section").strip("-")
