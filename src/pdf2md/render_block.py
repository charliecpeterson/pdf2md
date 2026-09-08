"""One block to one piece of Markdown, and the disposition that goes with it.

This is where the accounting invariant is actually enforced. Every branch of
`_render_block` returns a `CoverageStatus` alongside its text, and there is no
path that returns text without one — which is what makes
`CoverageReport.accounted_for` a check rather than a hope. A block that cannot be
represented emits a visible marker and is FLAGGED; it is never simply omitted.

The flags it raises carry the same discipline. A finding says what it can
support: an equation on a page with no text layer is "not verifiable", not
"unverified", because nothing on that page could have judged it. A two-character
block that will not decode is an "undecodable fragment", not illegible prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pdf2md.confidence import (
    HINT_MIN_CONF,
    RECOVER_BELOW,
)
from pdf2md.coverage import ILLEGIBLE_REASON, UNDECODABLE_FRAGMENT_REASON
from pdf2md.emit_figures import (
    _caption,
    _description,
    _figure_alt,
    _figure_labels,
    _plot_data,
    _write_plot_artifacts,
)
from pdf2md.emit_math import _equation_latex
from pdf2md.legibility import MIN_JUDGED_CHARS, is_garbage
from pdf2md.outline import heading_depth, is_label_heading
from pdf2md.schema import (
    PROSE_TYPES,
    Block,
    BlockType,
    CoverageFlag,
    CoverageStatus,
    Document,
    FigureRef,
    TableData,
)
from pdf2md.tables import panel_tables, render_table, table_has_content

_BOILERPLATE = {BlockType.PAGE_HEADER, BlockType.PAGE_FOOTER}

# A text layer unfit to judge one equation is unfit to transcribe any value on the
# same page, and the per-equation note says so one equation at a time. Promoting it
# to a document-level verdict is what routes a reader to --force-ocr, because the
# damage that matters most is in the tables, where the characters are wrong and
# every structural check reads clean.
# Two tables, not one: a single damaged table is already its own high-severity
# flag, and a document-level claim needs a pattern. Measured over 28 papers this
# fires on the four scans carrying real value damage and on none of the six
# born-digital papers whose equations are merely unjudgeable.
_MIN_CORRUPT_TABLES = 2

# A table whose cells carry wrong characters is itself evidence the layer is unfit,
# and on most papers it is the only evidence there is: the equation signal needs
# equations, and the paper that motivated this has one. Counting both is what lets
# a single unfit equation on page 4 warn about the numeric table on page 6.
_CORRUPT_CELL_KINDS = frozenset({"stray_glyphs_in_numeric_column", "decimal_separator_lost"})

_TITLE_PREFIX = re.compile(
    r"^(?:(?:part|chapter|appendix)\s+(?:\d+|[ivxlcdm]+)\b[.:]?\s*"
    r"|(?:\d+|[ivxlcdm]+)\b\s+)",
    re.I,
)


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


def _unfit_text_layer(doc: Document) -> CoverageFlag | None:
    """One verdict for the document when its own text layer cannot be trusted.

    Fired by tables whose cells carry wrong characters, because that is evidence
    about *values*. Unfit equations are reported alongside but cannot fire it: a
    layer can be unable to judge an equation and perfectly able to carry a table.
    Measured over 28 papers, six born-digital ones have every equation unjudgeable
    -- the Wiley and ACS substitution fonts that draw `(14)` as `ð14Þ` -- and no
    table damage at all. Telling those readers their values are unreliable, and to
    re-run with --force-ocr, would be wrong on the evidence.
    """
    corrupt = [
        t for t in doc.tables
        if any(f.get("kind") in _CORRUPT_CELL_KINDS
               for f in (t.grid_audit or {}).get("findings") or [])
    ]
    if len(corrupt) < _MIN_CORRUPT_TABLES:
        return None
    judged = [b for b in doc.blocks if "text_layer" in b.extra]
    unfit = [b for b in judged if not b.extra.get("ordered")]
    pages = sorted({t.page for t in corrupt} | {b.page for b in unfit})
    shown = ", ".join(str(p) for p in pages[:6]) + ("..." if len(pages) > 6 else "")
    return CoverageFlag(
        "#/document",
        pages[0],
        f"the embedded text layer is unfit to read: {len(corrupt)} table(s) whose "
        f"cells carry wrong characters"
        f"{f', and {len(unfit)} unverifiable equation region(s)' if unfit else ''}, "
        f"pages {shown}. Values transcribed from it are unreliable "
        f"even where the grid is structurally sound — a lost decimal point or a "
        f"digit read as a letter passes every arrangement check. Re-run with "
        f"--force-ocr for a fresh transcription, or --engine mineru where available; "
        f"the source crops beside each table are authoritative either way",
        "",
        disposition="action_required",
        severity="high",
        content_impact="high",
    )

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
        # Not `[^fn1]:` reference syntax. Nothing ever emitted the matching `[^fn1]`
        # in the body, so every definition rendered as a dangling note attached to
        # nothing -- four of them in one 1971 paper, including the qualification
        # that a radius is a crystal radius from Pauling. Placing the reference is
        # not reliably possible: the printed marker is usually a dagger the scan
        # read as a `t`. So the page's own marker is kept at the head of the note,
        # which is what a reader matches against, and no link is promised.
        append_part("---")
        append_part("**Footnotes**")
        for block, footnote in footnotes:
            start, end = append_part(f"- {footnote}")
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
        # The crop is authoritative and the grid still gets published under it.
        # Withholding the grid put 138 of 140 cropped tables behind an artifact
        # link, and emit already settled this question the other way for audited
        # tables: the content is present, the reader can check it against the image
        # sitting directly above, and the marker keeps it from being read as
        # unquestioned. A grid with nothing but a header is not worth the space.
        grid = ""
        if table is not None and table_has_content(table):
            rendered = panel_tables(table) or render_table(table)
            # A spanning table renders as HTML, which has no pipe rows; the
            # header-only test applies to GFM only, or it silently drops every
            # spanning table -- which is most of the cropped ones on a scan.
            pipes = [r for r in rendered.split("\n") if r.startswith("|")]
            if not pipes or len(pipes) >= 3:
                grid = f"\n\n{rendered}"
        out = (
            f"> **[pdf2md: {reason}]**\n\n![table]({crop})"
            + grid
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
        # The grid is emitted whatever the audit says -- the content is present,
        # and withholding it would lose data the reader can verify. The marker
        # rides above it so the table is never read as unquestioned.
        flag = _table_audit_flag(b, table)
        marker = f"{flag.marker_text}\n\n" if flag is not None else ""
        # A table set as repeated side-by-side panels is several tables, and the
        # merged grid puts a row from one panel beside a row from the next. Where
        # the split is unambiguous the panels are what a reader reaches first; the
        # merged grid stays available as the table artifact.
        panels = panel_tables(table)
        if panels is not None:
            note = (
                "> **[pdf2md: this table is typeset as repeated side-by-side panels "
                "and is emitted one grid per panel; the merged grid, in which a row "
                "of one panel sits beside a row of the next, is in the table "
                "artifact beside this file]**\n\n"
            )
            return (
                marker + note + panels + _table_source_links(table),
                CoverageStatus.EMITTED,
                flag,
            )
        return (
            marker + render_table(table) + _table_source_links(table),
            CoverageStatus.EMITTED,
            flag,
        )

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
                       withheld=(fig.data_path
                                 if fig.data_path.endswith(".withheld.csv") else ""),
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
        #
        # Unless there is no prose there to lose. A journal's decorative footer
        # glyph is a two-character block in a font with no usable encoding, and
        # calling that "illegible text layer" at high severity put five of them
        # at the top of one clean paper's review queue -- every high item it had.
        # `reading_order` and `record_block_recall` both already refuse to judge a
        # block this small; `MIN_JUDGED_CHARS` is the same floor for this one.
        if len(txt) < MIN_JUDGED_CHARS:
            return (
                _marker(b, UNDECODABLE_FRAGMENT_REASON),
                CoverageStatus.FLAGGED,
                _flag(b, UNDECODABLE_FRAGMENT_REASON,
                      disposition="informational", severity="low", content_impact="low"),
            )
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
                hint = _equation_latex(transcribed, b.extra.get("equation_number"))
                source = f"re-transcribed from the image ({by})" if by else "re-transcribed from the image"
            elif reading and b.extra.get("ordered") and (b.confidence or 0) >= HINT_MIN_CONF:
                hint, source = reading, "the image below is the authoritative source"
            elif txt:
                hint, source = (_equation_latex(txt, b.extra.get("equation_number")),
                                "the image below is the authoritative source")
            else:  # --no-formula: no LaTeX or text-layer reading, only the crop
                hint, source = "", "the image below is the authoritative source"
            if crop:
                intentional_crop = not ctx.formula_enrichment_enabled
                # A page whose text layer is scrambled or symbol-font garbage
                # cannot judge the LaTeX, so its disagreement is not evidence
                # the extraction is wrong. (Its *agreement* still is, which is
                # why the check keeps running against it.) Measured over the
                # equations from bundles with formula enrichment on, 52 of 61
                # "suspect" verdicts came from a layer the enrichment step had
                # already marked unfit to show, so calling them suspect
                # extractions was a claim the evidence did not support.
                #
                # A scanned page is the stronger form of the same case and was
                # landing in the harsher branch purely because it carries no
                # `text_layer` key to test: there is no layer at all, so nothing
                # can judge the LaTeX, and the verdict asked a reader to check
                # the extraction against a reference the page does not have. On
                # 28 papers converted at default settings that was 57 of the 120
                # image-backed equations, every one of them on a scan.
                scanned = bool(b.extra.get("ocr"))
                unverifiable = not intentional_crop and (
                    scanned or ("text_layer" in b.extra and not b.extra.get("ordered"))
                )
                why = (
                    "the page is scanned and has no text layer"
                    if scanned
                    else "the page's own text layer is scrambled or undecodable here"
                )
                headline = (
                    f"equation not verifiable — {why}, so it cannot judge the "
                    f"LaTeX; {source}"
                    if unverifiable
                    else f"equation extraction unverified — {source}"
                )
                note = f"> **[pdf2md: {headline}]**"
                body = f"{note}\n\n![equation]({crop})" + (f"\n\n{hint}" if hint else "")
                if intentional_crop:
                    reason, disposition, severity, impact = (
                        "equation: image is authoritative", "source_dependent", "none", "low",
                    )
                elif unverifiable:
                    # Informational, not an action. The LaTeX and the crop both
                    # ride with the block, so nothing is withheld from a reader
                    # -- every one of the 494 equations on formula-enabled
                    # documents here emits its LaTeX, and 277 carry a crop as
                    # well. What is missing is a verdict, and a verdict is
                    # missing for a reason that belongs to the page rather than
                    # to this equation: its text layer cannot judge LaTeX at
                    # all. Raised as an action it swamps triage without
                    # separating anything -- one 25-page maths paper produces 78
                    # identical entries, and 231 of them across the corpus. The
                    # marker still sits beside the equation, where a reader
                    # meets it at the point of use.
                    reason, disposition, severity, impact = (
                        "equation not verifiable: text layer unfit to judge",
                        "informational", "low", "low",
                    )
                else:
                    reason, disposition, severity, impact = (
                        "equation extraction unverified", "action_required", "medium", "high",
                    )
                return body, CoverageStatus.CROPPED, _flag(
                    b, reason, disposition=disposition,
                    severity=severity, content_impact=impact,
                )
            note = (
                "> **[pdf2md: equation extraction unverified — the rendering below "
                f"may differ from {_source_page(b.page)}]**"
            )
            return f"{note}\n\n{hint}", CoverageStatus.FLAGGED, _flag(b, "equation extraction unverified")
        return (_equation_latex(txt, b.extra.get("equation_number")),
                CoverageStatus.EMITTED, None)
    return txt, CoverageStatus.EMITTED, None

def _marker(b: Block, reason: str) -> str:
    return f"> **[pdf2md: {reason}]** {_source_page(b.page)}, block `{b.id}`"

def _source_page(page: int) -> str:
    return f"[source page {page}](../source.pdf#page={page})"

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

_AUDIT_SEVERITY_RANK = {"high": 2, "medium": 1}

def _table_audit_flag(b: Block, table: TableData) -> CoverageFlag | None:
    """One flag carrying every structural finding for this table, or None when
    the audit found nothing. The marker names each finding so the defect is
    legible where the table is read, not only in review.md."""
    findings = table.grid_audit.get("findings") or []
    if not findings:
        return None
    findings = sorted(
        findings, key=lambda f: -_AUDIT_SEVERITY_RANK.get(f.get("severity"), 0)
    )
    severity = findings[0].get("severity", "medium")
    reason = "table structure: " + ", ".join(
        dict.fromkeys(finding["kind"] for finding in findings)
    )
    details = "\n".join(f"> - {finding['detail']}" for finding in findings)
    marker = (
        f"> **[pdf2md: action required ({severity}): {reason}; verify against "
        f"{_source_page(b.page)}]**\n>\n{details}"
    )
    return CoverageFlag(
        b.id, b.page, reason, marker, "action_required", severity,
        "high" if severity == "high" else "medium",
    )

def _table_source_links(table: TableData) -> str:
    """Where to check this table: the printed region, and the data beside it."""
    links = [
        ("source crop", table.source_crop),
        ("CSV", table.data_path),
        ("JSON", table.json_path),
        ("glyph-truth grid", table.glyph_grid_path),
        ("printed lines", table.printed_lines_path),
    ]
    rendered = " · ".join(f"[{label}]({path})" for label, path in links if path)
    # The `*[pdf2md]` prefix marks the line as emitted navigation, so the
    # conservation audit doesn't count its link labels as words the source
    # never had.
    return f"\n\n*[pdf2md] table source:* {rendered}" if rendered else ""

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
