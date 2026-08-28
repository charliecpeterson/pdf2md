"""Serialize a Document to logical-section markdown files with YAML front-matter.

Walks blocks in reading order, sets each block's coverage_status as it renders,
and collects a visible marker for anything it can't represent (the coverage
invariant). Papers emit one `document.md`; books (bookmarks) split per chapter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from pdf2md.confidence import (
    HINT_MIN_CONF,
    PLOT_DATA_MIN_CONFIDENCE,
    RECOVER_BELOW,
    plot_data_accepted,
)
from pdf2md.coverage import ILLEGIBLE_REASON
from pdf2md.legibility import is_garbage
from pdf2md.logging import Progress
from pdf2md.outline import heading_depth, is_label_heading
from pdf2md.schema import (
    FORMAT_VERSION,
    PROSE_TYPES,
    Block,
    BlockType,
    CoverageFlag,
    CoverageStatus,
    Document,
    FigureRef,
    Section,
    TableData,
)
from pdf2md.structure import is_chapter_container
from pdf2md.table_artifacts import write_table_artifacts
from pdf2md.tables import render_table, table_has_content

_BOILERPLATE = {BlockType.PAGE_HEADER, BlockType.PAGE_FOOTER}

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
    version_dir: Path = Path(".")
    footnotes: list[str] = field(default_factory=list)
    flags: list[CoverageFlag] = field(default_factory=list)
    # Per-file heading state, reset by `_render_blocks`: how much to deepen body
    # headings (so they nest under the file-title H1), headings to suppress
    # (file-title duplicates, label headings merged into the next), and override text
    # (a label heading merged with its title).
    base_depth: int = 0
    head_skip: set[str] = field(default_factory=set)
    head_text: dict[str, str] = field(default_factory=dict)
    headings: list[tuple[int, str, int]] = field(default_factory=list)  # (level, text, page) for the index
    page_rasters: dict[int, str] = field(default_factory=dict)  # scanned page -> asset relpath
    formula_enrichment_enabled: bool = True
    emission_index: dict[str, dict] | None = None
    markdown_path: str | None = None


@dataclass(frozen=True)
class _OutlineEntry:
    filename: str
    title: str
    headings: list[tuple[int, str, int]]
    depth: int


# Strip a leading "Part/Chapter/Appendix" word and/or a standalone number or roman
# numeral so "Part IV: Issues …" and the bookmark title "IV Issues …" compare equal.
# The bare numeral must be followed by whitespace, so an initial like "C. elegans" (a
# period, not a space) keeps its "C" instead of being read as a section numeral. `\b`
# keeps a real word ("Introduction") from losing its leading "I".
_TITLE_PREFIX = re.compile(
    r"^(?:(?:part|chapter|appendix)\s+(?:\d+|[ivxlcdm]+)\b[.:]?\s*"
    r"|(?:\d+|[ivxlcdm]+)\b\s+)",
    re.I,
)


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _TITLE_PREFIX.sub("", t.strip().lower())).strip()


def _heading_plan(blocks: list[Block], title: str) -> tuple[set[str], dict[str, str]]:
    """Decide which heading blocks to drop or rewrite for one file: drop a heading
    that just restates the file title (the bookmark title and the page heading are
    the same line), and fold a bare 'Chapter N' label into the title heading that
    follows it."""
    skip: set[str] = set()
    text: dict[str, str] = {}
    headings = [b for b in blocks if b.type == BlockType.HEADING and b.text.strip()]
    norm_title = _norm_title(title)
    for i, b in enumerate(headings):
        h = b.text.strip()
        if norm_title and _norm_title(h) == norm_title:
            skip.add(b.id)
        elif is_label_heading(h) and i + 1 < len(headings):
            nxt = headings[i + 1]
            if norm_title and _norm_title(nxt.text) == norm_title:
                # "Part IV" + "Issues of convergence …" together just restate the file
                # title — drop both rather than merge them into a duplicate heading.
                skip.add(b.id)
                skip.add(nxt.id)
            else:
                text[b.id] = f"{h}: {nxt.text.strip()}"
                skip.add(nxt.id)
    return skip, text


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


def _render_blocks(blocks: list[Block], ctx: _Ctx, *, title: str = "", base_depth: int = 0) -> str:
    ctx.base_depth = base_depth
    ctx.head_skip, ctx.head_text = _heading_plan(blocks, title)
    ctx.headings = []
    parts: list[str] = []
    footnotes: list[tuple[Block, str]] = []
    body_length = 0

    def append_part(part: str) -> tuple[int, int]:
        nonlocal body_length
        if parts:
            body_length += 2
        start = body_length
        parts.append(part)
        body_length += len(part)
        return start, body_length

    prev_page: int | None = None
    for b in blocks:
        if b.page != prev_page:
            append_part(f"<!-- page {b.page} -->")
            raster = ctx.page_rasters.get(b.page)
            if raster:  # scanned page: link its image so OCR prose can be verified
                append_part(f"[page {b.page} scan]({raster})")
            prev_page = b.page
        text, status, flag = _render_block(b, ctx, footnotes)
        # OCR uncertainty rides on top of the per-type render, so a scanned heading keeps its
        # level (and TOC entry) and a footnote still lands in the footnotes section — the flag
        # just makes the uncertainty visible and counts the block as flagged (never silent).
        reasons = []
        if b.extra.get("ocr_disagreement"):
            reasons.append("re-reads disagreed on the numbers")
        if b.extra.get("ocr_cap_truncated"):
            reasons.append("hit the output token cap — the tail may be missing")
        if b.extra.get("ocr_loop_truncated"):
            reasons.append("a repetition loop was trimmed")
        if reasons:
            note = (
                f"> **[pdf2md: OCR uncertain — {'; '.join(reasons)}; verify against "
                f"{_source_page(b.page)}]**"
            )
            if text is not None:
                text = f"{note}\n\n{text}"
            status = CoverageStatus.FLAGGED
            flag = flag or _flag(b, f"OCR uncertain: {'; '.join(reasons)}")
        b.coverage_status = status
        if flag is not None:
            ctx.flags.append(flag)
        if ctx.emission_index is not None:
            emitted_text = b.text if b.type is BlockType.FOOTNOTE and text is None else text or ""
            ctx.emission_index[b.id] = {
                "markdown": ctx.markdown_path,
                "text": emitted_text,
                "intentional_omission": (
                    b.type in _BOILERPLATE
                    or b.id in ctx.head_skip
                    or b.id in ctx.head_text
                    or bool(b.extra.get("figure_caption_of"))
                ),
            }
        if text:
            start, end = append_part(text)
            if ctx.emission_index is not None:
                ctx.emission_index[b.id].update(body_start=start, body_end=end)
    if footnotes:
        append_part("---")
        for index, (block, footnote) in enumerate(footnotes, start=1):
            start, end = append_part(f"[^fn{index}]: {footnote}")
            if ctx.emission_index is not None:
                ctx.emission_index[block.id].update(body_start=start, body_end=end)
    return "\n\n".join(parts)


def _render_block(
    b: Block, ctx: _Ctx, footnotes: list[tuple[Block, str]]
) -> tuple[str | None, CoverageStatus, CoverageFlag | None]:
    txt = b.text.strip()
    if b.extra.get("figure_caption_of"):
        return None, CoverageStatus.EMITTED, None

    if b.type in _BOILERPLATE:  # intentionally stripped, not lost
        return None, CoverageStatus.EMITTED, None

    # A console transcript enrich re-read line-preserved (Docling mislabelled it
    # prose): emit in a code fence so the layout survives reading-order collapse.
    if b.extra.get("preformatted") and txt:
        return f"```\n{b.text}\n```", CoverageStatus.EMITTED, None

    # A table Docling couldn't parse to cells still has a bbox; the pipeline cropped
    # it, so emit the image rather than dropping the region (equations carry their
    # own crop handling in the EQUATION branch below).
    crop = b.extra.get("crop_path")
    table = ctx.tables.get(b.id)
    if crop and b.type is not BlockType.EQUATION:
        if (b.extra.get("cells_unverified") and table is not None
                and table_has_content(table)):
            reason = (
                "table read from the image by the engine — no embedded text backs "
                "these cells; the image is authoritative and the candidate below "
                "requires review"
            )
        elif b.extra.get("ocr") and table is not None and table_has_content(table):
            reason = (
                "scanned table — the image is authoritative; the structured OCR "
                "candidate below requires review"
            )
        else:
            reason = ("scanned page — the image is the source, the OCR text is unreliable"
                      if b.extra.get("ocr")
                      else "table not extracted as text — the image below is the source")
        out = (
            f"> **[pdf2md: {reason}]**\n\n![table]({crop})"
            + (_table_candidate_links(table) if table is not None else "")
            + _description(b.extra.get("description"))
        )
        disposition = (
            "action_required"
            if b.extra.get("cells_unverified") or b.extra.get("ocr")
            else "source_dependent"
        )
        return out, CoverageStatus.CROPPED, _flag(
            b,
            "table candidate unverified" if disposition == "action_required"
            else "table: image is authoritative",
            disposition=disposition,
            severity="medium" if disposition == "action_required" else "none",
            content_impact="high" if disposition == "action_required" else "low",
        )

    if b.extra.get("cells_unverified") and table is not None and table_has_content(table):
        reason = ("glyph-unbacked table crop unavailable; verify the engine's cells "
                  "against the source page")
        out = f"> **[pdf2md: {reason}]**" + _table_candidate_links(table)
        return out, CoverageStatus.FLAGGED, _flag(b, "unverified table crop unavailable")

    if b.extra.get("ocr") and table is not None and table_has_content(table):
        reason = "scanned table crop unavailable; verify the OCR candidate against the source page"
        out = f"> **[pdf2md: {reason}]**" + _table_candidate_links(table)
        return out, CoverageStatus.FLAGGED, _flag(b, "scanned table crop unavailable")

    # Render parsed table data wherever it exists, even when Docling labelled the
    # block something other than TABLE (TOC pages come through as `other` but still
    # carry cells) — otherwise the content is orphaned and the block dropped.
    if table is not None:
        if table.preformatted:  # ASCII-art table -> code fence, not a mangled grid
            return f"```\n{table.preformatted}\n```", CoverageStatus.EMITTED, None
        return render_table(table), CoverageStatus.EMITTED, None

    if b.type == BlockType.FIGURE:
        fig = ctx.figures.get(b.id)
        if fig and fig.asset_path:
            alt = _figure_alt(fig.caption)
            svg = f"\n\n[figure as SVG (lossless vector)]({fig.svg_path})" if fig.svg_path else ""
            out = (f"![{alt}]({fig.asset_path})" + svg + _caption(fig.caption)
                   + _description(fig.description) + _figure_labels(fig.labels)
                   + _plot_data(
                       fig.digitization,
                       fig.caption,
                       fig.labels,
                       artifacts=_write_plot_artifacts(ctx.version_dir, fig),
                       status=fig.data_extraction_status,
                       status_note=fig.data_extraction_note,
                   ))
            return out, CoverageStatus.CROPPED, None
        return _marker(b, "figure crop missing"), CoverageStatus.FLAGGED, _flag(b, "figure crop missing")

    if b.type == BlockType.TABLE:  # labelled a table but no cells parsed and no crop
        return _marker(b, "table not extracted"), CoverageStatus.FLAGGED, _flag(b, "table not extracted")

    if b.type == BlockType.FOOTNOTE:
        if txt and is_garbage(txt):  # a broken-font footnote is garbage like any prose
            return _marker(b, ILLEGIBLE_REASON), CoverageStatus.FLAGGED, _flag(b, ILLEGIBLE_REASON)
        if txt:
            footnotes.append((b, txt))
        return None, CoverageStatus.EMITTED, None

    if not txt and not b.extra.get("crop_path"):
        return _marker(b, f"empty {b.type.value} block"), CoverageStatus.DROPPED, _flag(b, "empty block")

    if b.type in PROSE_TYPES and is_garbage(txt):
        # enrich's pdfium refill couldn't rescue this block (the glyph layer was
        # garbage too). Emit a visible marker so the coverage audit counts it as
        # illegible instead of passing symbol-font noise off as readable prose.
        return _marker(b, ILLEGIBLE_REASON), CoverageStatus.FLAGGED, _flag(b, ILLEGIBLE_REASON)

    if b.type == BlockType.HEADING:
        if b.id in ctx.head_skip:  # duplicates the file title, or merged into a label
            return None, CoverageStatus.EMITTED, None
        text = ctx.head_text.get(b.id, txt)
        level = max(1, min((ctx.depth_of.get(b.id) or heading_depth(b)) + ctx.base_depth, 6))
        ctx.headings.append((level, text, b.page))
        return f"{'#' * level} {text}", CoverageStatus.EMITTED, None
    if b.type == BlockType.LIST:
        return f"- {txt}", CoverageStatus.EMITTED, None
    if b.type == BlockType.CAPTION:
        return f"*{txt}*", CoverageStatus.EMITTED, None
    if b.type == BlockType.CODE:
        return f"```\n{b.text}\n```", CoverageStatus.EMITTED, None
    if b.type == BlockType.EQUATION:
        crop = b.extra.get("crop_path")
        if crop or (b.confidence is not None and b.confidence < RECOVER_BELOW):
            # Either the cross-check couldn't verify the text extraction, or there's no
            # text at all (--no-formula): the cropped image is the authoritative source
            # whenever we have one. The hint below is the best available text: a multi-pass
            # re-transcription of the crop if we have one, else the clean text-layer reading,
            # else the vision LaTeX (never scrambled token soup). The image stays the source.
            transcribed = b.extra.get("transcribed")
            reading = b.extra.get("text_layer")
            if transcribed:
                by = b.extra.get("transcribed_source")
                hint = _equation_latex(transcribed)
                source = f"re-transcribed from the image ({by})" if by else "re-transcribed from the image"
            elif reading and b.extra.get("ordered") and (b.confidence or 0) >= HINT_MIN_CONF:
                hint, source = reading, "the image below is the authoritative source"
            elif txt:
                hint, source = _equation_latex(txt), "the image below is the authoritative source"
            else:  # --no-formula: no LaTeX or text-layer reading, only the crop
                hint, source = "", "the image below is the authoritative source"
            if crop:
                note = f"> **[pdf2md: equation extraction unverified — {source}]**"
                body = f"{note}\n\n![equation]({crop})" + (f"\n\n{hint}" if hint else "")
                intentional_crop = not ctx.formula_enrichment_enabled
                return body, CoverageStatus.CROPPED, _flag(
                    b,
                    "equation: image is authoritative" if intentional_crop
                    else "equation extraction unverified",
                    disposition="source_dependent" if intentional_crop else "action_required",
                    severity="none" if intentional_crop else "medium",
                    content_impact="low" if intentional_crop else "high",
                )
            note = (
                "> **[pdf2md: equation extraction unverified — the rendering below "
                f"may differ from {_source_page(b.page)}]**"
            )
            return f"{note}\n\n{hint}", CoverageStatus.FLAGGED, _flag(b, "equation extraction unverified")
        return _equation_latex(txt), CoverageStatus.EMITTED, None
    return txt, CoverageStatus.EMITTED, None


def _marker(b: Block, reason: str) -> str:
    return f"> **[pdf2md: {reason}]** {_source_page(b.page)}, block `{b.id}`"


def _source_page(page: int) -> str:
    return f"[source page {page}](../source.pdf#page={page})"


_TABLE_REF = re.compile(r"[Tt]ables?\s*([A-Z]?\d+[a-z]?|[IVXLC]+\b)")


def _table_xref(caption, labels) -> str:
    """A pointer to the printed data table when the figure's own text names one ('Listed
    in Table 5'). For a figure whose plot data couldn't be extracted, the table — which
    the pipeline extracts losslessly — is usually the authoritative text form of the same
    numbers, so say so where a reader (human or LLM) will look."""
    text = " ".join(t for t in (caption, labels.text if labels else None) if t)
    m = _TABLE_REF.search(text)
    if not m:
        return ""
    return (f"\n\n> **[pdf2md: the figure's text points at Table {m.group(1)} — "
            "the printed table is the authoritative data for this figure]**")


def _plot_data(
    dig,
    caption=None,
    labels=None,
    *,
    artifacts=None,
    status: str = "not_attempted",
    status_note: str = "",
) -> str:
    """Link or inline accepted chart data and explain why rejected data is absent."""
    if dig is None:
        marker = ""
        if status and status != "not_attempted":
            detail = f" {status_note}" if status_note else ""
            marker = f"\n\n> **[pdf2md: plot data not extracted: {status}]**{detail}"
        return marker + _table_xref(caption, labels)
    if not dig.series:  # a gated read: the pre-scan vetoed digitization, say why
        return (f"\n\n> **[pdf2md: plot data not extracted — {dig.method}]** {dig.note}"
                + _table_xref(caption, labels))
    head = (f"> **[pdf2md: extracted plot data — {dig.method}, "
            f"confidence {dig.confidence:.2f}]** {dig.note}")
    if dig.verify_asset:  # round-trip: original vs reconstruction, for a human eyeball check
        head += f"\n\n![original vs reconstruction]({dig.verify_asset})"
    if not plot_data_accepted(dig):
        return (f"\n\n{head}\n\n> **[pdf2md: data withheld — confidence below "
                f"{PLOT_DATA_MIN_CONFIDENCE:.2f}; read the values off the image above]**"
                + _table_xref(caption, labels))
    if artifacts:
        data_path, code_path = artifacts
        links = (
            f"[plot data (CSV)]({data_path}) · "
            f"[reproduction script (Python)]({code_path})"
        )
        return f"\n\n{head}\n\n{links}"
    return f"\n\n{head}\n\n{_plot_data_csv(dig)}\n\n{_plot_script(dig, caption, labels)}"


def _write_plot_artifacts(version_dir: Path, figure: FigureRef) -> tuple[str, str] | None:
    dig = figure.digitization
    figure.data_path = ""
    figure.code_path = ""
    if not plot_data_accepted(dig):
        return None

    stem = Path(figure.asset_path).stem if figure.asset_path else re.sub(
        r"[^a-zA-Z0-9]+", "_", figure.block_id
    ).strip("_")
    data_dir = version_dir / "data"
    code_dir = version_dir / "code"
    data_dir.mkdir(exist_ok=True)
    code_dir.mkdir(exist_ok=True)
    figure.data_path = f"data/{stem}.csv"
    figure.code_path = f"code/{stem}.py"

    csv_text = _plot_data_csv(dig).removeprefix("```csv\n").removesuffix("\n```")
    script = _plot_script(dig, figure.caption, figure.labels)
    script_text = script.removeprefix("```python\n").removesuffix("\n```")
    (version_dir / figure.data_path).write_text(csv_text + "\n")
    (version_dir / figure.code_path).write_text(script_text + "\n")
    return figure.data_path, figure.code_path


def _plot_data_csv(dig) -> str:
    """The recovered series as CSV, with the per-axis scale (linear/log) as a header so the
    numbers are unambiguous rather than bare x,y columns. Axis titles aren't included here —
    they come through --figure-labels; see Digitization."""
    blocks = [f"# x scale: {dig.x_kind}\n# y scale: {dig.y_kind}"]
    for i, series in enumerate(dig.series, 1):
        rows = "\n".join(f"{x},{y}" for x, y in series)
        name = dig.series_names[i - 1] if dig.series_names else f"series {i}"
        blocks.append(f"# {name}\nx,y\n{rows}")
    return "```csv\n" + "\n\n".join(blocks) + "\n```"


_SCRIPT_LABEL_LINES = 20  # a labels dump is context, not data; don't let it swamp the script


def _script_context(caption, labels) -> list[str]:
    """The figure's verified/recovered printed text as comment lines at the top of the repro
    script, so the script alone carries what the plot says (axis titles, legend, caption) —
    as comments, not set_xlabel guesses, because which line is which axis isn't known."""
    lines = []
    if caption:
        lines.append(f"# caption: {' '.join(caption.split())}")
    if labels is not None and labels.text:
        kept = [ln for ln in labels.text.splitlines() if ln.strip()]
        lines.append("# printed on the figure (recovered labels):")
        lines += [f"#   {ln}" for ln in kept[:_SCRIPT_LABEL_LINES]]
        if len(kept) > _SCRIPT_LABEL_LINES:
            lines.append(f"#   ... {len(kept) - _SCRIPT_LABEL_LINES} more (see labels above)")
    return lines + [""] if lines else []


def _plot_script(dig, caption=None, labels=None) -> str:
    """A self-contained matplotlib script that redraws the plot from the recovered data —
    assembled from the extracted series and axis scale, NOT generated by a model, so it
    reproduces the numbers we read rather than inventing any."""
    lines = ["import matplotlib.pyplot as plt", ""]
    lines += _script_context(caption, labels)
    lines += ["series = ["]
    lines += [f"    {[[x, y] for x, y in s]!r}," for s in dig.series]
    lines += ["]", "", "fig, ax = plt.subplots()"]
    if dig.kind == "bar":
        lines += ["for s in series:",
                  "    xs, ys = zip(*s)",
                  "    w = 0.8 * min((b - a for a, b in zip(xs, xs[1:])), default=1.0)",
                  "    ax.bar(xs, ys, width=w)"]
    else:
        linestyle = "'none'" if dig.kind == "scatter" else "'-'"
        lines += ["for s in series:",
                  "    xs, ys = zip(*s)",
                  f"    ax.plot(xs, ys, marker='o', linestyle={linestyle})"]
    if dig.x_kind == "log":
        lines.append("ax.set_xscale('log')")
    if dig.y_kind == "log":
        lines.append("ax.set_yscale('log')")
    lines += ["ax.set_xlabel('x')", "ax.set_ylabel('y')", "plt.show()"]
    return "```python\n" + "\n".join(lines) + "\n```"


def _figure_labels(fl) -> str:
    """Printed text read off a figure (--figure-labels), confidence-tagged, below the image.
    Empty when there's none."""
    if fl is None:
        return ""
    # The source (text layer vs OCR) rides in the note, which differs per tier.
    head = f"> **[pdf2md: printed figure labels, confidence {fl.confidence:.2f}]** {fl.note}"
    return f"\n\n{head}\n\n{fl.text}"


def _caption(text: str | None) -> str:
    """The figure's own caption as visible text below the image. It otherwise lives only in
    the image alt attribute, which a reader scanning the markdown never sees — so the figure
    isn't text-sufficient without it. Authoritative document text (from the PDF, not AI/OCR),
    so it's rendered plainly in italics, without the [pdf2md: ...] annotation the generated
    layers carry."""
    return f"\n\n*{text}*" if text else ""


_FIGURE_ALT_LABEL = re.compile(
    r"^\s*((?:fig(?:ure)?\.?)\s*[A-Za-z]?\d+(?:[.\-]\d+)*(?:[a-z])?)",
    re.IGNORECASE,
)


def _figure_alt(caption: str | None) -> str:
    """Keep the image label useful without copying the full adjacent caption."""
    match = _FIGURE_ALT_LABEL.match(caption or "")
    return _clean_alt(match.group(1) if match else "figure")


def _description(text: str | None) -> str:
    """A VLM crop description (`--describe`), labelled as generated and placed below
    the image. Empty when there's none, so it appends cleanly. The content rides
    outside the marker blockquote so a transcribed GFM table still renders."""
    return f"\n\n> **[pdf2md: AI-generated description]**\n\n{text}" if text else ""


def _table_candidate_links(table: TableData) -> str:
    links = [
        ("Markdown", table.candidate_path),
        ("CSV", table.data_path),
        ("JSON", table.json_path),
        ("normalized CSV", table.normalized_data_path),
        ("normalized JSON", table.normalized_json_path),
        ("cell evidence", table.cell_evidence_path),
    ]
    rendered = " · ".join(f"[{label}]({path})" for label, path in links if path)
    return f"\n\nStructured OCR candidate: {rendered}" if rendered else ""


def _flag(
    b: Block,
    reason: str,
    *,
    disposition: str = "action_required",
    severity: str = "medium",
    content_impact: str = "medium",
) -> CoverageFlag:
    if reason in {ILLEGIBLE_REASON, "empty block", "unplaced block"}:
        severity = "high"
        content_impact = "high"
    return CoverageFlag(
        b.id,
        b.page,
        reason,
        _marker(b, reason),
        disposition,
        severity,
        content_impact,
    )


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


def _clean_alt(s: str) -> str:
    return re.sub(r"\s+", " ", s).replace("[", "(").replace("]", ")").strip()


def _slug(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s.lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s)
    return (s[:50] or "section").strip("-")
