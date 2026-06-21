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

from pdf2md.confidence import HINT_MIN_CONF, RECOVER_BELOW
from pdf2md.coverage import ILLEGIBLE_REASON
from pdf2md.legibility import is_garbage
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

# Prose-bearing types held to the legibility bar. A block here whose text is still
# symbol-font garbage after enrich's pdfium refill gets a visible marker, never a
# silent emit (equations/tables/code carry their own non-prose representations).
_PROSE = {BlockType.PARAGRAPH, BlockType.HEADING, BlockType.LIST,
          BlockType.CAPTION, BlockType.OTHER}

# Docling encodes trailing PDF whitespace and lost alignment columns as long runs
# of LaTeX spacing commands (\quad, control-spaces) or empty `& \quad` cells, which
# render as a wall of gaps. The (?<!\\) guard keeps `\\` line breaks intact.
_MATH_SPACE = r"(?:(?<!\\)\\(?:qquad|quad|[,;:! ])|~)"
_MATH_RUN = re.compile(rf"{_MATH_SPACE}(?:\s*{_MATH_SPACE})+")
_MATH_TAIL = re.compile(rf"(?:{_MATH_SPACE}|\s|\\|&)+$")
_MATH_EMPTY_CELLS = re.compile(rf"(?:&\s*{_MATH_SPACE}\s*){{2,}}")


def _tidy_math(body: str) -> str:
    body = _MATH_EMPTY_CELLS.sub(" & ", body)
    body = _MATH_TAIL.sub("", body)
    body = _MATH_RUN.sub(r" \\quad ", body).strip()
    return _balance_braces(body)


def _equation_latex(text: str) -> str:
    body = _balance_delims(_tidy_math(text.strip("$").strip()))
    # Alignment markers (&, \\) are only valid inside an environment; bare $$ makes
    # KaTeX/MathJax throw. Wrap multi-line equations in `aligned`.
    if "&" in body or r"\\" in body:
        body = f"\\begin{{aligned}}\n{body}\n\\end{{aligned}}"
    return f"$$\n{body}\n$$"


def _balance_delims(body: str) -> str:
    """KaTeX throws on a `\\left` without a matching `\\right` (Docling sometimes
    emits `\\left⟨ … \\right| … \\right⟩`, two `\\right` for one `\\left`). When the
    pair is unbalanced, drop the auto-sizing commands; the bare delimiters still
    render, just without stretching."""
    if len(re.findall(r"\\left(?![a-zA-Z])", body)) != len(re.findall(r"\\right(?![a-zA-Z])", body)):
        body = re.sub(r"\\left(?![a-zA-Z])|\\right(?![a-zA-Z])", "", body)
    return body


def _balance_braces(body: str) -> str:
    """KaTeX dumps the raw source for an unbalanced `{`/`}`, which happens when
    Docling garbles an equation (a misread `}` as `)`, say). Pad the missing
    side so the expression still renders instead of showing as literal TeX."""
    opens = len(re.findall(r"(?<!\\)\{", body))
    closes = len(re.findall(r"(?<!\\)\}", body))
    if opens > closes:
        return body + "}" * (opens - closes)
    if closes > opens:
        return "{" * (closes - opens) + body
    return body


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
    # Drop null-valued keys: Quarto's schema rejects `doi: null` / `authors: null`
    # (a field declared as a string can't be null), failing the whole render.
    front = {k: v for k, v in {**base_front, "section_title": title}.items() if v is not None}
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

    # A table Docling couldn't parse to cells still has a bbox; the pipeline cropped
    # it, so emit the image rather than dropping the region (equations carry their
    # own crop handling in the EQUATION branch below).
    crop = b.extra.get("crop_path")
    if crop and b.type is not BlockType.EQUATION:
        reason = ("scanned page — the image is the source, the OCR text is unreliable"
                  if b.extra.get("ocr")
                  else "table not extracted as text — the image below is the source")
        return f"> **[pdf2md: {reason}]**\n\n![table]({crop})", CoverageStatus.CROPPED, _flag(b, "table image fallback")

    # Render parsed table data wherever it exists, even when Docling labelled the
    # block something other than TABLE (TOC pages come through as `other` but still
    # carry cells) — otherwise the content is orphaned and the block dropped.
    table = ctx.tables.get(b.id)
    if table is not None:
        return render_table(table), CoverageStatus.EMITTED, None

    if b.type == BlockType.FIGURE:
        fig = ctx.figures.get(b.id)
        if fig and fig.asset_path:
            alt = _clean_alt(fig.caption or "figure")
            return f"![{alt}]({fig.asset_path})", CoverageStatus.CROPPED, None
        return _marker(b, "figure crop missing"), CoverageStatus.FLAGGED, _flag(b, "figure crop missing")

    if b.type == BlockType.TABLE:  # labelled a table but no cells parsed and no crop
        return _marker(b, "table not extracted"), CoverageStatus.FLAGGED, _flag(b, "table not extracted")

    if b.type == BlockType.FOOTNOTE:
        if txt:
            footnotes.append(txt)
        return None, CoverageStatus.EMITTED, None

    if not txt:
        return _marker(b, f"empty {b.type.value} block"), CoverageStatus.DROPPED, _flag(b, "empty block")

    if b.type in _PROSE and is_garbage(txt):
        # enrich's pdfium refill couldn't rescue this block (the glyph layer was
        # garbage too). Emit a visible marker so the lossless audit counts it as
        # illegible instead of passing symbol-font noise off as readable prose.
        return _marker(b, ILLEGIBLE_REASON), CoverageStatus.FLAGGED, _flag(b, ILLEGIBLE_REASON)

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
        if b.confidence is not None and b.confidence < RECOVER_BELOW:
            # The cross-check could not verify this equation's text extraction, so
            # the cropped image is emitted as the authoritative source. The hint
            # below is the best available text: a multi-pass re-transcription of the
            # crop if we have one, else the clean text-layer reading, else the vision
            # LaTeX (never scrambled token soup). The image stays the source.
            transcribed = b.extra.get("transcribed")
            reading = b.extra.get("text_layer")
            if transcribed:
                hint, source = _equation_latex(transcribed), "re-transcribed from the image (math OCR)"
            elif reading and b.extra.get("ordered") and b.confidence >= HINT_MIN_CONF:
                hint, source = reading, "the image below is the authoritative source"
            else:
                hint, source = _equation_latex(txt), "the image below is the authoritative source"
            crop = b.extra.get("crop_path")
            if crop:
                note = f"> **[pdf2md: equation extraction unverified — {source}]**"
                return f"{note}\n\n![equation]({crop})\n\n{hint}", CoverageStatus.CROPPED, _flag(b, "equation: image is authoritative")
            note = "> **[pdf2md: equation extraction unverified — the rendering below may differ from the source]**"
            return f"{note}\n\n{hint}", CoverageStatus.FLAGGED, _flag(b, "equation extraction unverified")
        return _equation_latex(txt), CoverageStatus.EMITTED, None
    return txt, CoverageStatus.EMITTED, None


def _marker(b: Block, reason: str) -> str:
    return f"> **[pdf2md: {reason}]** page {b.page}, block `{b.id}`"


def _flag(b: Block, reason: str) -> CoverageFlag:
    return CoverageFlag(b.id, b.page, reason, _marker(b, reason))


def _front_matter(doc: Document, meta: dict, section_source: str, engine_versions: dict) -> dict:
    front = {
        "format_version": FORMAT_VERSION,
        "title": meta.get("title"),
        "authors": meta.get("authors"),
        "year": meta.get("year"),
        "doi": meta.get("doi"),
        "source": Path(doc.source_path).name,
        "doc_id": doc.doc_id[:16],
        "pages": doc.page_count,
        "section_source": section_source,
        # not "engine": that key is reserved by Quarto's YAML front-matter.
        "engine_versions": engine_versions,
    }
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
                    if b.type in _PROSE and b.text.strip() and is_garbage(b.text))
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


def _ordered(blocks: list[Block], ids: set[str]) -> list[Block]:
    return [b for b in blocks if b.id in ids]


def _clean_alt(s: str) -> str:
    return re.sub(r"\s+", " ", s).replace("[", "(").replace("]", ")").strip()


def _slug(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s.lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s)
    return (s[:50] or "section").strip("-")
