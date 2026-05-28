from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from docsmcp.citation import format_apa as _format_apa, format_bibtex as _format_bibtex
from docsmcp.image import render_block_crop, render_page
from docsmcp.index import fts as _fts
from docsmcp.pipeline import ingest_dir as _ingest_dir, transcribe as _transcribe
from docsmcp.postprocess.normalize import ocr_confusable_variants as _ocr_confusable_variants
from docsmcp.triage import summarize as _triage_summarize, triage_pdf as _triage_pdf

mcp = FastMCP("docsmcp")


@mcp.tool
def ingest_file(
    path: str,
    profile: str = "balanced",
    force: bool = False,
    verify: bool = False,
    verify_visual: bool = False,
    kind: str = "document",
    tags: list[str] | None = None,
    title: str | None = None,
    authors: str | None = None,
    year: int | None = None,
) -> dict[str, Any]:
    """Ingest a single PDF or image: transcribe → chunk → embed → index → enrich metadata.

    Metadata is auto-resolved via CrossRef when a DOI is found in the document text;
    falls back to heuristic title/author/year extraction. Pass explicit values to override.

    Args:
        path: Absolute path to a PDF or image file.
        profile: "fast" | "balanced" | "max_accuracy" (DPI + formula/code enrichment).
        force: Re-run even if a cached transcription exists.
        verify: Also run Marker as a second engine and produce a disagreement report.
        verify_visual: After transcription, run the local Qwen3-VL model to (a) verify
            equation numbers on flagged equations and (b) extract title/authors/year
            from page 1 for docs without a DOI. Adds ~1-2 min for math-heavy papers.
        kind: Free-form label, suggested: "textbook" | "paper" | "note" | "document".
        tags: Optional list of tags for later filtering.
        title/authors/year: Override the auto-resolved metadata.

    Returns:
        Result with doc_id, version, paths, page_count, block_count, triage,
        and (if verify_visual=True) visual_verify summary.
    """
    return _transcribe(
        path,
        profile=profile,
        force=force,
        verify=verify,
        verify_visual=verify_visual,
        kind=kind,
        tags=tags,
        title=title,
        authors=authors,
        year=year,
    )


@mcp.tool
def transcribe(
    path: str,
    profile: str = "balanced",
    force: bool = False,
    verify: bool = False,
    kind: str = "document",
    tags: list[str] | None = None,
    title: str | None = None,
    authors: str | None = None,
    year: int | None = None,
) -> dict[str, Any]:
    """Deprecated alias for ingest_file (kept for older MCP clients)."""
    return _transcribe(
        path,
        profile=profile,
        force=force,
        verify=verify,
        kind=kind,
        tags=tags,
        title=title,
        authors=authors,
        year=year,
    )


@mcp.tool
def ingest_dir(
    path: str,
    profile: str = "balanced",
    kind: str = "document",
    tags: list[str] | None = None,
    pattern: str = "*.pdf",
    verify: bool = False,
) -> dict[str, Any]:
    """Bulk-ingest every file matching `pattern` under `path` (recursive).

    Use this to add a folder of papers or a directory of scanned chapters.
    """
    return _ingest_dir(
        path, profile=profile, kind=kind, tags=tags, pattern=pattern, verify=verify
    )


@mcp.tool
def triage(path: str) -> dict[str, Any]:
    """Fast (no-OCR) classification of each page as born_digital / mixed / scanned / empty."""
    src = Path(path).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"No such file: {src}")
    pages = _triage_pdf(src)
    return {
        "path": str(src),
        "page_count": len(pages),
        "summary": _triage_summarize(pages),
        "pages": [asdict(p) for p in pages],
    }


_CONFIDENT_RERANK_THRESHOLD = 0.3


def _search_envelope(results: list[dict[str, Any]], suggest: list[str]) -> dict[str, Any]:
    """Wrap a search result list with confidence + next-step hints."""
    out: dict[str, Any] = {"results": results, "suggested_next": suggest}
    if not results:
        out["top_score"] = 0.0
        out["confident"] = False
        out["matched_terms"] = False
        return out
    # Pop the FTS-degraded flag set by index.search_chunks
    fts_degraded = bool(results[0].pop("_fts_degraded", False))
    scores = [r.get("rerank_score") for r in results if r.get("rerank_score") is not None]
    top = max(scores) if scores else 0.0
    has_fts = any(r.get("fts_score") is not None for r in results)
    out["top_score"] = float(top)
    out["matched_terms"] = bool(has_fts)
    out["confident"] = (bool(has_fts) and not fts_degraded) or top >= _CONFIDENT_RERANK_THRESHOLD
    if fts_degraded:
        out["fts_degraded"] = True
        out["note"] = (
            "FTS5 keyword matching failed for this query (typically punctuation). "
            "Results are vector-similarity only; matched_terms reflects pre-degradation state."
        )
    return out


@mcp.tool
def search_chunks(
    query: str,
    top_k: int = 5,
    doc_id: str | None = None,
    kind: str | None = None,
    mode: str = "hybrid",
    rerank: bool = True,
    only_verified: bool = False,
    compact: bool = False,
) -> dict[str, Any]:
    """Find passages relevant to `query` (hybrid FTS5 + vector + cross-encoder rerank).

    Use this for "what page in this textbook discusses the Lorentz force?" — every result
    carries doc_id, page range, section, verify status, and chunk_id you can pass to
    `cite()`, `get_chunk()`, or `get_page_image()`.

    Args:
        query: Natural-language or keyword query.
        top_k: Max results to return.
        doc_id: If set, restrict to one document (full hash or short prefix).
        kind: Filter by doc kind ("textbook" | "paper" | ...).
        mode: "hybrid" (default) | "fts" | "vector".
        rerank: Apply cross-encoder reranking (default True).
        only_verified: Exclude chunks whose blocks were flagged by cross-engine
            disagreement (verify in {"disagreement", "flagged"}).
        compact: If True, return only {chunk_id, doc_id, title, page_first, page_last,
            section, snippet[:240], verify, refs}. Use for tokens-light overview;
            follow up with get_chunk(chunk_id) for full text.

    Response envelope:
        results: list of hits (each with chunk_id, page, section, text/snippet, scores).
        top_score: best rerank score (or 0 if no rerank).
        matched_terms: True if FTS5 found at least one literal-term match. If False
            AND `confident` is False, the hits are vector-nearest-neighbor only —
            treat with skepticism.
        confident: heuristic — True iff `matched_terms` OR `top_score >= 0.3`.
        suggested_next: tool calls that typically follow this one.
    """
    results = _fts.search_chunks(
        query,
        top_k=top_k,
        doc_id=doc_id,
        kind=kind,
        mode=mode,
        rerank=rerank,
        only_verified=only_verified,
        compact=compact,
    )
    envelope = _search_envelope(
        results,
        suggest=["get_chunk", "cite", "get_page", "get_page_image"],
    )
    # Structural-keyword fast path: if the query looks like "abstract" / "conclusion"
    # / "methods" and we have an outline match, surface the actual chunks in that
    # section. Without this, vector + rerank often miss the right section entirely
    # when the section's text doesn't literally mention the keyword.
    if doc_id and _fts._is_structural_query(query):
        try:
            section = get_section(doc_id, query, include_markdown=False)
            if section and "error" not in section:
                envelope["section_hit"] = {
                    "heading": section.get("heading"),
                    "page": section.get("page"),
                    "next_section_page": section.get("next_section_page"),
                    "tip": "use get_section(doc_id, query, include_markdown=True) for the full text",
                }
                # Inject chunks from this section if they aren't already in results.
                # Prefer chunks whose section field matches the target heading;
                # fall back to other chunks in the same page range only if needed.
                page_first = section.get("page", 0)
                end_page = section.get("next_section_page") or (page_first + 6)
                page_last = max(page_first, end_page - 1)
                target_heading = (section.get("heading") or "").lower()
                section_chunks = _fts.get_chunks_by_pages(doc_id, page_first, page_last)

                def _matches_target(chunk_section: str | None) -> bool:
                    if not chunk_section or not target_heading:
                        return False
                    cs = chunk_section.lower()
                    return cs == target_heading or cs in target_heading or target_heading in cs

                primary = [c for c in section_chunks if _matches_target(c.get("section"))]
                # If no chunk's section name matches (e.g., reading order put section
                # heading in the middle of a chunk), fall back to ALL chunks in range.
                candidates = primary if primary else section_chunks

                seen_ids = {r.get("chunk_id") for r in envelope["results"]}
                injected = []
                for c in candidates:
                    if c["chunk_id"] in seen_ids:
                        continue
                    if compact:
                        snippet = c["text"]
                        if len(snippet) > 240:
                            snippet = snippet[:240].rstrip() + "…"
                        c = {
                            "chunk_id": c["chunk_id"],
                            "doc_id": c["doc_id"],
                            "title": c.get("title"),
                            "page_first": c["page_first"],
                            "page_last": c["page_last"],
                            "section": c["section"],
                            "snippet": snippet,
                            "verify": "unverified",
                            "from_section_hit": True,
                        }
                    else:
                        c["from_section_hit"] = True
                    injected.append(c)
                if injected:
                    envelope["results"] = injected + envelope["results"]
                    envelope["results"] = envelope["results"][:top_k]
                    envelope["confident"] = True
        except Exception:
            pass
    return envelope


@mcp.tool
def search_docs(
    query: str,
    top_k: int = 5,
    kind: str | None = None,
    rerank: bool = True,
    chunks_per_doc: int = 3,
) -> dict[str, Any]:
    """Find documents that discuss `query` (aggregates chunk hits per document).

    Use this for "which papers in my library talk about superconductivity in copper oxides?"
    Returns docs ranked by aggregate relevance with their best matching chunks attached.
    """
    return {
        "results": _fts.search_docs(
            query,
            top_k=top_k,
            kind=kind,
            rerank=rerank,
            chunks_per_doc=chunks_per_doc,
        ),
        "suggested_next": ["search_chunks (with doc_id)", "get_outline", "get_citation"],
    }


@mcp.tool
def get_chunk(chunk_id: str) -> dict[str, Any]:
    """Retrieve a single chunk by ID, with prev/next links and source metadata."""
    c = _fts.get_chunk(chunk_id)
    if c is None:
        return {"error": f"chunk_id not found: {chunk_id}"}
    return c


@mcp.tool
def cite(block_id: str) -> dict[str, Any]:
    """Resolve a block_id to full provenance (page, bbox, source path)."""
    b = _fts.get_block(block_id)
    if b is None:
        return {"error": f"block_id not found: {block_id}"}
    return b


@mcp.tool
def get_page(
    doc_id: str, page: int, include_bbox: bool = False, include_blocks: bool = True
) -> dict[str, Any]:
    """Return one page's content as assembled markdown, plus optionally per-block details.

    Args:
        doc_id: Doc hash (full or short prefix).
        page: 1-indexed page number.
        include_bbox: Default False. When True, each block carries its bbox.
            Small models rarely use bboxes — leave off to save tokens.
        include_blocks: Default True. When False, returns only the assembled
            markdown without the per-block list.
    """
    blocks = _fts.get_page_blocks(doc_id, page)
    md = "\n\n".join(b["text"] for b in blocks if b["text"])
    out: dict[str, Any] = {"doc_id": doc_id, "page": page, "markdown": md}
    if include_blocks:
        if include_bbox:
            out["blocks"] = blocks
        else:
            out["blocks"] = [{k: v for k, v in b.items() if k != "bbox"} for b in blocks]
    return out


@mcp.tool
def search_figures(query: str, doc_id: str | None = None, top_k: int = 10) -> dict[str, Any]:
    """Find figures whose caption or surrounding text matches `query` (substring, case-insensitive)."""
    return {"results": _fts.search_figures(query, doc_id=doc_id, top_k=top_k)}


@mcp.tool
def search_tables(query: str, doc_id: str | None = None, top_k: int = 10) -> dict[str, Any]:
    """Find tables whose caption or surrounding text matches `query` (substring, case-insensitive)."""
    return {"results": _fts.search_tables(query, doc_id=doc_id, top_k=top_k)}


@mcp.tool
def search_equations(query: str, doc_id: str | None = None, top_k: int = 10) -> dict[str, Any]:
    """Find equations whose LaTeX or surrounding text matches `query` (substring, case-insensitive)."""
    return {"results": _fts.search_equations(query, doc_id=doc_id, top_k=top_k)}


@mcp.tool
def get_outline(doc_id: str) -> dict[str, Any]:
    """Return the heading hierarchy of a doc: list of {text, page, depth, block_id}.

    Use this as the entry point for question-answering — much cheaper than scanning
    pages to figure out what sections a paper has.
    """
    outline = _fts.get_outline(doc_id)
    return {
        "outline": outline,
        "suggested_next": ["get_section", "get_page", "search_chunks"],
    }


_SECTION_SYNONYMS: dict[str, list[str]] = {
    "abstract": ["abstract"],
    "introduction": ["introduction", "intro"],
    "methods": ["method", "methods", "methodology", "computational methods", "experimental section"],
    "results": ["results", "results and discussion", "discussion"],
    "conclusion": ["conclusion", "conclusions", "summary", "summary and perspective"],
    "references": ["references", "bibliography", "works cited"],
    "appendix": ["appendix"],
    "acknowledgments": ["acknowledgments", "acknowledgements"],
}

# Reverse lookup: any variant → full set of synonyms (so 'conclusions' → all forms).
_SECTION_SYNONYMS_INV: dict[str, list[str]] = {}
for _canonical, _variants in _SECTION_SYNONYMS.items():
    for _v in _variants:
        _SECTION_SYNONYMS_INV[_v.lower()] = _variants


@mcp.tool
def get_section(doc_id: str, name: str, include_markdown: bool = True) -> dict[str, Any]:
    """Find a section by name (fuzzy match) and return its page range + assembled text.

    Accepts common variants: "abstract", "intro", "methods", "results", "conclusion",
    "references", "appendix" — plus any literal substring match against headings
    in the outline.

    Returns {heading, page, depth, page_range, markdown?, next_section_page?}.
    """
    name_lower = (name or "").strip().lower()
    outline = _fts.get_outline(doc_id)
    if not outline:
        return {"error": "no outline available"}

    # Build candidate match list: explicit synonyms + literal substring.
    # The reverse map ensures plural/variant forms (e.g., "conclusions") still
    # resolve to the full synonym set.
    targets = set(_SECTION_SYNONYMS_INV.get(name_lower, []))
    if not targets:
        targets = {name_lower}

    matches = []
    for i, h in enumerate(outline):
        h_text = h["text"].lower()
        for t in targets:
            if t in h_text:
                matches.append((i, h))
                break

    if not matches:
        return {
            "error": f"no section matches {name!r}",
            "available_headings": [h["text"] for h in outline],
        }

    idx, heading = matches[0]
    next_page = outline[idx + 1]["page"] if idx + 1 < len(outline) else None
    out: dict[str, Any] = {
        "heading": heading["text"],
        "page": heading["page"],
        "depth": heading["depth"],
        "next_section_heading": outline[idx + 1]["text"] if idx + 1 < len(outline) else None,
        "next_section_page": next_page,
        "block_id": heading["block_id"],
    }
    if include_markdown:
        # Assemble all pages from heading.page through next_section.page (inclusive)
        end_page = next_page or heading["page"]
        text_parts = []
        for p in range(heading["page"], end_page + 1):
            blocks = _fts.get_page_blocks(doc_id, p)
            text_parts.append("\n\n".join(b["text"] for b in blocks if b["text"]))
        out["markdown"] = "\n\n".join(text_parts).strip()
        out["page_range"] = [heading["page"], end_page]
    return out


@mcp.tool
def get_figure_image(
    doc_id: str,
    number: str | None = None,
    figure_id: str | None = None,
    dpi: int = 220,
    padding_pts: float = 8.0,
) -> dict[str, Any]:
    """Resolve a figure by number/id and return a PNG crop of its bbox in one call.

    Shortcut for `get_figure → get_block_image` (saves a round-trip for the common case).
    """
    if number is None and figure_id is None:
        return {"error": "Pass either `number` or `figure_id`"}
    f = _fts.get_figure(doc_id, number=number, figure_id=figure_id)
    if f is None:
        return {"error": f"figure not found (doc={doc_id}, number={number})"}
    if not f.get("bbox"):
        return {"error": "figure has no bbox; can't crop"}

    docs = _fts.list_docs()
    doc = next((d for d in docs if d["doc_id"] == f["doc_id"]), None)
    if doc is None or not doc.get("source_path"):
        return {"error": "source PDF metadata missing"}
    src = Path(doc["source_path"])
    if not src.exists():
        return {"error": f"source PDF missing on disk: {src}"}
    try:
        path = render_block_crop(
            src,
            f["doc_id"],
            f["page"],
            f["bbox"],
            block_id=f["figure_id"],
            dpi=dpi,
            padding_pts=padding_pts,
        )
    except Exception as e:
        return {"error": str(e)}
    return {
        "figure_id": f["figure_id"],
        "number": f["number"],
        "caption": f.get("caption"),
        "panels": f.get("panels", []),
        "page": f["page"],
        "dpi": dpi,
        "path": str(path),
    }


@mcp.tool
def list_library(kind: str | None = None) -> dict[str, Any]:
    """List all transcribed documents (filterable by kind)."""
    return {"docs": _fts.list_docs(kind=kind)}


@mcp.tool
def set_metadata(
    doc_id: str,
    title: str | None = None,
    authors: str | None = None,
    year: int | None = None,
    kind: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Override or add metadata for a doc after ingest."""
    return _fts.set_metadata(
        doc_id, title=title, authors=authors, year=year, kind=kind, tags=tags
    )


@mcp.tool
def list_equations(
    doc_id: str,
    page: int | None = None,
    only_numbered: bool = False,
    summary: bool = False,
) -> dict[str, Any]:
    """List all display equations extracted from a doc.

    Use this for "what equations are in this paper?" or "list all equations in chapter 3".
    Each entry includes the equation number (when present), cleaned LaTeX,
    page number, and surrounding context paragraphs.

    Args:
        doc_id: Doc hash (full or short prefix).
        page: Restrict to one page.
        only_numbered: Skip unnumbered display equations.
        summary: If True, omit latex_raw, bbox, and context fields. Returns just
            number/page/latex_clean/flags. Use this for "what equations does this
            paper have?" without burning tokens on every full LaTeX body.
    """
    return {
        "equations": _fts.list_equations(
            doc_id, page=page, only_numbered=only_numbered, summary=summary
        ),
        "suggested_next": ["get_equation", "get_equation_by_label", "verify_equations"],
    }


@mcp.tool
def list_tables(
    doc_id: str, page: int | None = None, summary: bool = False
) -> dict[str, Any]:
    """List all tables extracted from a doc.

    Each entry includes table number (when present), caption, row/col counts,
    page, and markdown body. Use `summary=True` for a tokens-light overview.
    """
    return {
        "tables": _fts.list_tables(doc_id, page=page, summary=summary),
        "suggested_next": ["get_table", "get_table_by_label"],
    }


@mcp.tool
def get_table(
    doc_id: str, number: str | None = None, table_id: str | None = None
) -> dict[str, Any]:
    """Look up one table by its number (e.g., "1") or stable table_id."""
    if number is None and table_id is None:
        return {"error": "Pass either `number` or `table_id`"}
    t = _fts.get_table(doc_id, number=number, table_id=table_id)
    if t is None:
        return {"error": f"table not found (doc={doc_id}, number={number}, table_id={table_id})"}
    return t


_TABLE_LABEL_RE = __import__("re").compile(
    r"(?i)\b(?:table|tab\.?)\s*([0-9]+(?:\.[0-9]+)*[a-z]?)"
)


@mcp.tool
def get_table_by_label(doc_id: str, label: str) -> dict[str, Any]:
    """Resolve "Table 1" / "Tab. 3.2" / "table 4a" / "(1)" → matching table."""
    s = (label or "").strip()
    m = _TABLE_LABEL_RE.search(s) or _PAREN_NUM_RE.search(s)
    if m:
        number = m.group(1)
    else:
        stripped = s.strip("()")
        if stripped and all(c.isdigit() or c in ".ab" for c in stripped):
            number = stripped
        else:
            return {"error": f"could not parse table label: {label!r}"}
    t = _fts.get_table(doc_id, number=number)
    if t is None:
        return {"error": f"no table matches label {label!r} → number {number!r}"}
    return t


@mcp.tool
def list_figures(
    doc_id: str, page: int | None = None, summary: bool = False
) -> dict[str, Any]:
    """List all captioned figures in a doc (decorative/uncaptioned figures are skipped).

    Each entry includes figure number, caption, page, and bbox for cropping.
    Pair with `get_block_image(block_id)` to render the actual figure.
    """
    return {
        "figures": _fts.list_figures(doc_id, page=page, summary=summary),
        "suggested_next": ["get_figure_image", "get_figure_by_label", "get_block_image"],
    }


@mcp.tool
def get_figure(
    doc_id: str, number: str | None = None, figure_id: str | None = None
) -> dict[str, Any]:
    """Look up one figure by number or figure_id."""
    if number is None and figure_id is None:
        return {"error": "Pass either `number` or `figure_id`"}
    f = _fts.get_figure(doc_id, number=number, figure_id=figure_id)
    if f is None:
        return {"error": f"figure not found (doc={doc_id}, number={number})"}
    return f


_FIG_LABEL_RE = __import__("re").compile(
    r"(?i)\b(?:figure|fig\.?)\s*([0-9]+(?:\.[0-9]+)*[a-z]?)"
)


@mcp.tool
def get_figure_by_label(doc_id: str, label: str) -> dict[str, Any]:
    """Resolve "Figure 1" / "Fig. 3a" / "(2)" → matching figure.

    Falls back to OCR-confusable variants (1 ↔ I ↔ l, 0 ↔ O, 5 ↔ S) when the
    exact number isn't found — useful for papers where OCR misread the figure
    number's first digit (e.g., "Figure 1.1" captured as "Figure 1.I").
    """
    s = (label or "").strip()
    m = _FIG_LABEL_RE.search(s) or _PAREN_NUM_RE.search(s)
    if m:
        number = m.group(1)
    else:
        stripped = s.strip("()")
        if stripped and all(c.isdigit() or c in ".abIlOS" for c in stripped):
            number = stripped
        else:
            return {"error": f"could not parse figure label: {label!r}"}
    f = _fts.get_figure(doc_id, number=number)
    if f is None:
        for variant in _ocr_confusable_variants(number):
            if variant == number:
                continue
            f = _fts.get_figure(doc_id, number=variant)
            if f is not None:
                f["matched_via_variant"] = variant
                return f
        return {
            "error": f"no figure matches label {label!r} → number {number!r}",
            "tried_variants": _ocr_confusable_variants(number),
        }
    return f


@mcp.tool
def get_citation(doc_id: str, style: str = "plain") -> dict[str, Any]:
    """Return formatted citation info for a doc (authors/title/year/journal/volume/pages/doi).

    Args:
        doc_id: Doc hash (full or short prefix).
        style: "plain" (default — return raw fields) or "apa"/"bibtex" (formatted string).
    """
    docs = _fts.list_docs()
    doc = next((d for d in docs if d["doc_id"] == doc_id or d["doc_id"].startswith(doc_id)), None)
    if doc is None:
        return {"error": f"doc_id not found: {doc_id}"}
    fields = {
        "doc_id": doc["doc_id"],
        "title": doc.get("title"),
        "authors": doc.get("authors"),
        "year": doc.get("year"),
        "journal": doc.get("journal"),
        "volume": doc.get("volume"),
        "pages": doc.get("pages"),
        "doi": doc.get("doi"),
        "kind": doc.get("kind"),
    }
    if style == "plain":
        return fields
    if style == "apa":
        fields["formatted"] = _format_apa(doc)
        return fields
    if style == "bibtex":
        fields["formatted"] = _format_bibtex(doc)
        return fields
    return {"error": f"unknown style: {style}"}


@mcp.tool
def verify_metadata(doc_id: str, force: bool = False) -> dict[str, Any]:
    """Use a local Qwen3-VL model to read page 1 and extract title/authors/year.

    Best used for non-paper docs (textbooks, arXiv preprints, reports) where the
    DOI-based CrossRef path doesn't apply. Skips docs that already have a DOI by
    default (CrossRef is more authoritative than VLM); pass force=True to override.
    """
    return _fts.verify_metadata(doc_id, force=force)


@mcp.tool
def verify_table_captions(
    doc_id: str, limit: int | None = None, force: bool = False
) -> dict[str, Any]:
    """Visually verify that each table's caption actually describes the table.

    For each table with a bbox and caption, crops the region and asks the local
    Qwen3-VL model "does this caption match?". Catches caption-pairing errors
    (table N labeled with caption from table N+1 due to layout ambiguity).

    Args:
        doc_id: Doc to verify.
        limit: Cap on how many tables to check.
        force: Re-check tables that already have a vlm_caption_match.

    Returns: {checked, matched, mismatched, uncertain, skipped_no_caption, skipped_no_bbox}.
    Each table's `get_table`/`list_tables` response now carries `vlm_caption: {match, confidence}`.
    """
    return _fts.verify_table_captions(doc_id, limit=limit, force=force)


@mcp.tool
def verify_figure_captions(
    doc_id: str, limit: int | None = None, force: bool = False
) -> dict[str, Any]:
    """Visually verify that each figure's caption actually describes the image.

    Same shape as `verify_table_captions` but applied to the figures index.
    Useful for catching figure-caption swaps in densely-laid-out papers.
    """
    return _fts.verify_figure_captions(doc_id, limit=limit, force=force)


@mcp.tool
def verify_table_cells(
    doc_id: str,
    table_number: str | None = None,
    only_numeric: bool = True,
    max_per_table: int = 8,
    force: bool = False,
) -> dict[str, Any]:
    """Per-cell verification: VLM reads (row, col) cells from each table and compares
    to the heuristic-extracted value.

    Catches OCR errors in cell values (1OO → 100, transposed digits, dropped
    decimals). Renders the whole-table crop once per table; the VLM locates each
    queried cell within that image.

    Args:
        doc_id: Doc to verify.
        table_number: If set, verify only this table; else all tables.
        only_numeric: Skip non-numeric cells (focus on data values).
        max_per_table: Cap on cells checked per table.
        force: Re-verify cells that already have a stored result.

    Returns: {tables_processed, cells_checked, matched, mismatched, uncertain, ...}.
    Fetch detailed per-cell results via `get_table_cell_verifications`.
    """
    return _fts.verify_table_cells(
        doc_id,
        table_number=table_number,
        only_numeric=only_numeric,
        max_per_table=max_per_table,
        force=force,
    )


@mcp.tool
def get_table_cell_verifications(
    doc_id: str, table_number: str | None = None
) -> dict[str, Any]:
    """List stored per-cell verification results for a doc, optionally one table."""
    return {
        "verifications": _fts.get_table_cell_verifications(
            doc_id, table_number=table_number
        )
    }


@mcp.tool
def verify_equation_latex(
    doc_id: str,
    only_flagged: bool = True,
    limit: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Visually verify that each equation's extracted LaTeX represents the source image.

    Complements `verify_equations` (which checks only the number). This catches
    OCR damage that number-verification misses: whitespace runaways, column
    bleed, truncated content, wrong-symbol scrambles. Asks the local Qwen3-VL
    "does this LaTeX represent the equation?" with tolerance for formatting
    differences (m_{0} vs m_0, etc.).

    Args:
        doc_id: Doc to verify.
        only_flagged: If True (default), check only equations with `inferred=True`
            or any flag set (the suspicious ones). Set False to verify everything.
        limit: Cap on how many equations to check.
        force: Re-check equations that already have a vlm_latex_match.

    Returns: {checked, matched, mismatched, uncertain, skipped_no_bbox, skipped_no_latex}.
    Equations get a `vlm_latex: {match, confidence}` field in their list/get responses.
    """
    return _fts.verify_equation_latex(
        doc_id, only_flagged=only_flagged, limit=limit, force=force
    )


@mcp.tool
def verify_equations(
    doc_id: str,
    only_flagged: bool = True,
    limit: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Visually verify equation numbers using a local Qwen3-VL model.

    Crops each equation's bbox from the source PDF and asks the VLM to read the
    printed equation number. Compares to our heuristic-extracted number; if they
    disagree, the VLM result wins and `number` is overwritten. Results are stored
    so subsequent list_equations / get_equation calls expose a `vlm` field.

    Args:
        doc_id: Doc to verify.
        only_flagged: If True (default), skip equations with directly-extracted
            numbers and no flags — only re-checks `inferred`, `repeat_loop_truncated`,
            `column_bleed_stripped`, etc.
        limit: Cap on how many to check (None = all).
        force: Re-run even if previously verified.

    Returns: {verified, confirmed, mismatch, missing, unchanged}.
    Default model: Qwen3-VL-4B-Instruct-4bit (override with DOCSMCP_VLM_MODEL env var).
    First call downloads ~3GB and warms the model (~30s); subsequent calls are fast.
    """
    return _fts.verify_equations(
        doc_id, only_flagged=only_flagged, limit=limit, force=force
    )


@mcp.tool
def get_equation(
    doc_id: str, number: str | None = None, eq_id: str | None = None
) -> dict[str, Any]:
    """Look up one equation by its number (e.g. "2.46") or eq_id.

    Returns cleaned LaTeX, the raw LaTeX as the engine emitted it, the surrounding
    "context_before" / "context_after" paragraphs, page number, and bbox for cropping.
    """
    if number is None and eq_id is None:
        return {"error": "Pass either `number` (e.g., '2.46') or `eq_id`"}
    eq = _fts.get_equation(doc_id, number=number, eq_id=eq_id)
    if eq is None:
        return {"error": f"equation not found (doc={doc_id}, number={number}, eq_id={eq_id})"}
    return eq


_EQ_LABEL_RE = __import__("re").compile(
    r"(?i)\b(?:equation|eq\.?)\s*\(?\s*([0-9]+(?:\.[0-9]+)*[a-z]?)\s*\)?"
)
_PAREN_NUM_RE = __import__("re").compile(r"\(\s*([0-9]+(?:\.[0-9]+)*[a-z]?)\s*\)")


@mcp.tool
def get_equation_by_label(doc_id: str, label: str) -> dict[str, Any]:
    """Resolve a freeform equation reference and return the matching equation.

    Accepts forms like "eq 18", "equation 2.46", "(15)", or just "18". Useful when
    a downstream LLM reads "as shown in eq 18" and wants the actual content.
    """
    s = (label or "").strip()
    m = _EQ_LABEL_RE.search(s)
    if not m:
        m = _PAREN_NUM_RE.search(s)
    if not m:
        # bare number?
        stripped = s.strip("()")
        if stripped and all(c.isdigit() or c in ".ab" for c in stripped):
            number = stripped
        else:
            return {"error": f"could not parse equation label: {label!r}"}
    else:
        number = m.group(1)
    eq = _fts.get_equation(doc_id, number=number)
    if eq is None:
        return {
            "error": f"no equation matches label {label!r} → number {number!r} in doc {doc_id}"
        }
    return eq


@mcp.tool
def get_page_image(doc_id: str, page: int, dpi: int = 200) -> dict[str, Any]:
    """Render one page of a doc to a PNG and return its path.

    Use this when you need to actually see the source: spot-check an OCR result,
    inspect a figure or chart, or feed a page image to a vision model.
    Results are cached under out/{doc}/v{n}/pages/.
    """
    doc = _resolve_doc(doc_id)
    if doc is None:
        return {"error": f"doc_id not found: {doc_id}"}
    src = Path(doc["source_path"])
    if not src.exists():
        return {"error": f"source PDF missing on disk: {src}"}
    try:
        path = render_page(src, doc["doc_id"], page, dpi=dpi)
    except Exception as e:
        return {"error": str(e)}
    return {
        "doc_id": doc["doc_id"],
        "page": page,
        "dpi": dpi,
        "path": str(path),
    }


@mcp.tool
def get_block_image(block_id: str, dpi: int = 220, padding_pts: float = 6.0) -> dict[str, Any]:
    """Crop the source PDF region for a single block and return the PNG path.

    Use this to visually verify an equation, table, or figure cited by `search_chunks`.
    Requires the block's bbox (Docling provides this; Marker blocks may lack bbox).
    """
    b = _fts.get_block(block_id)
    if b is None:
        return {"error": f"block_id not found: {block_id}"}
    if not b.get("bbox"):
        return {"error": "block has no bbox (engine did not record it)"}
    src = Path(b["source_path"])
    if not src.exists():
        return {"error": f"source PDF missing on disk: {src}"}
    try:
        path = render_block_crop(
            src,
            b["doc_id"],
            b["page"],
            b["bbox"],
            block_id=block_id,
            dpi=dpi,
            padding_pts=padding_pts,
        )
    except Exception as e:
        return {"error": str(e)}
    return {
        "block_id": block_id,
        "doc_id": b["doc_id"],
        "page": b["page"],
        "bbox": b["bbox"],
        "dpi": dpi,
        "path": str(path),
    }


def _resolve_doc(doc_id: str) -> dict[str, Any] | None:
    for d in _fts.list_docs():
        if d["doc_id"] == doc_id or d["doc_id"].startswith(doc_id):
            return d
    return None


@mcp.tool
def reindex() -> dict[str, Any]:
    """Rebuild the index (blocks + chunks + embeddings) from on-disk provenance files."""
    return _fts.reindex_all()


@mcp.tool
def diagnose_schema(doc_id: str | None = None) -> dict[str, Any]:
    """Scan all JSON-stored columns for corruption. Reports any malformed rows.

    Use when search results look wrong or empty — corrupted JSON cells now degrade
    silently (returning defaults) instead of crashing, but this tool surfaces them.

    Returns: {json_column_totals, json_corruption: [...], applied_migrations,
    all_migrations_count, healthy}. If `healthy: false`, re-ingest the listed docs.
    """
    return _fts.diagnose_schema(doc_id=doc_id)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
