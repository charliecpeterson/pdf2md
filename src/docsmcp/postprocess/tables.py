from __future__ import annotations

import hashlib
import json as _json
import re
from dataclasses import dataclass, field
from typing import Any


_TABLE_LABEL_RE = re.compile(
    r"(?i)\bTable\s*"
    r"([IVXLCDM]+|[0-9]+(?:\.(?:[0-9]+|[IVXLCDM]+))*[a-z]?)"
    r"[\s.:(]"
)
_NEIGHBOR_WINDOW = 3

_FOOTNOTE_MARKER_AT_START = re.compile(r"^([a-z])\s+[A-Z\[\(*]")
_REF_CITATION = re.compile(r"\bref\s+\d+", re.IGNORECASE)
_MARKER_FIND = re.compile(r"(?:^|\s)([a-z])\s+(?=\S)")


def _row_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_separator_row(line: str) -> bool:
    return bool(re.match(r"^\s*\|[\s\-:|]+\|\s*$", line))


def _looks_like_footnote_row(line: str) -> bool:
    """Detect a row that's actually a footnote leaked into the table grid.

    Triggers when ANY of:
      1. First non-empty cell starts with single-letter footnote marker (a/b/c + space + uppercase/bracket)
      2. Two adjacent non-empty cells contain identical long text (OCR cell-merge artifact)
      3. Row contains 'ref N' style citations
    """
    cells = _row_cells(line)
    non_empty = [c for c in cells if c]
    if not non_empty:
        return False
    if _FOOTNOTE_MARKER_AT_START.match(non_empty[0]):
        return True
    for i in range(len(non_empty) - 1):
        if non_empty[i] == non_empty[i + 1] and len(non_empty[i]) > 15:
            return True
    if _REF_CITATION.search(" ".join(non_empty)):
        return True
    return False


def _split_into_footnotes(text: str) -> list[dict[str, str]]:
    """Split a deduped footnote-row blob into individual footnotes by marker."""
    text = text.strip()
    if not text:
        return []
    matches = list(_MARKER_FIND.finditer(text))
    if not matches:
        if re.match(r"^[a-z]\s+", text):
            return [{"marker": text[0], "text": text[2:].strip()}]
        return []
    out: list[dict[str, str]] = []
    seen_markers: set[str] = set()
    for i, m in enumerate(matches):
        marker = m.group(1)
        if marker in seen_markers:
            continue
        seen_markers.add(marker)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip().rstrip(",;")
        if content:
            out.append({"marker": marker, "text": content})
    return out


def _extract_footnotes(markdown: str) -> tuple[str, str | None, list[dict[str, str]]]:
    """Strip footnote pseudo-rows from a table markdown.

    Returns (cleaned_markdown, footnotes_raw_or_None, parsed_footnotes_list).
    """
    lines = markdown.splitlines()
    if len(lines) < 3:
        return markdown, None, []
    # Walk backward from the last row; collect contiguous footnote-rows
    footnote_idxs: list[int] = []
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i]
        if not line.strip().startswith("|"):
            continue
        if _is_separator_row(line):
            break
        if _looks_like_footnote_row(line):
            footnote_idxs.append(i)
        else:
            break
    if not footnote_idxs:
        return markdown, None, []
    footnote_idxs.sort()
    keep = [ln for i, ln in enumerate(lines) if i not in footnote_idxs]
    cleaned = "\n".join(keep).rstrip()

    # Dedup cells in the footnote rows and join
    parts: list[str] = []
    for i in footnote_idxs:
        for c in _row_cells(lines[i]):
            c = c.strip()
            if not c:
                continue
            if parts and parts[-1] == c:
                continue
            parts.append(c)
    raw = " ".join(parts).strip()
    parsed = _split_into_footnotes(raw)
    return cleaned, raw or None, parsed


@dataclass
class ParsedTable:
    table_id: str
    doc_id: str
    seq: int
    number: str | None
    inferred: bool
    caption: str | None
    markdown: str
    n_rows: int
    n_cols: int
    page: int
    block_id: str
    bbox_json: str | None
    context_before: str | None = None
    context_after: str | None = None
    flags: list[str] = field(default_factory=list)
    footnotes_raw: str | None = None
    footnotes: list[dict[str, str]] = field(default_factory=list)


def _table_id(doc_id: str, seq: int, md: str) -> str:
    return "t" + hashlib.sha1(f"{doc_id}:{seq}:{md[:200]}".encode()).hexdigest()[:12]


def _measure_table(md: str) -> tuple[int, int]:
    """Return (rows, cols) for a markdown table. Cheap, doesn't validate."""
    if not md:
        return 0, 0
    lines = [ln for ln in md.splitlines() if ln.strip().startswith("|")]
    rows = len(lines)
    if not rows:
        return 0, 0
    # Skip header separator like |---|---|
    body = [ln for ln in lines if not re.match(r"^\s*\|[\s\-:|]+\|\s*$", ln)]
    rows = len(body)
    cells = [c.strip() for c in lines[0].strip().strip("|").split("|")]
    return rows, len(cells)


def _find_caption(blocks: list[dict[str, Any]], i: int) -> tuple[str | None, str | None]:
    """Look for a caption block within ±_NEIGHBOR_WINDOW of `i`. Returns (caption_text, number)."""
    from docsmcp.postprocess.roman import normalize_label_number

    n = len(blocks)
    for delta in range(1, _NEIGHBOR_WINDOW + 1):
        for j in (i - delta, i + delta):
            if 0 <= j < n:
                b = blocks[j]
                btype = (b.get("type") or "").lower()
                text = (b.get("text") or "").strip()
                if not text:
                    continue
                if btype in ("caption", "paragraph"):
                    m = _TABLE_LABEL_RE.match(text)
                    if m:
                        return text, normalize_label_number(m.group(1))
                    if btype == "caption" and text.lower().startswith("table"):
                        return text, None
    return None, None


def _nearest_paragraph(blocks: list[dict[str, Any]], i: int, direction: int) -> str | None:
    """Find the nearest paragraph block in the given direction (+1 = after, -1 = before)."""
    n = len(blocks)
    step = 1 if direction > 0 else -1
    j = i + step
    while 0 <= j < n:
        b = blocks[j]
        if (b.get("type") or "") == "paragraph" and (b.get("text") or "").strip():
            return (b["text"] or "").strip()
        j += step
    return None


def _infer_numbers(tables: list[ParsedTable]) -> None:
    """Fill in missing table numbers from neighbors. Uses lenient anchor parsing
    so labels like '1.1' contribute as anchor 1."""
    from docsmcp.postprocess.inference import infer_numbers_table

    infer_numbers_table(tables)


def parse_tables(doc_id: str, blocks: list[dict[str, Any]]) -> list[ParsedTable]:
    out: list[ParsedTable] = []
    seq = 0
    for i, b in enumerate(blocks):
        if (b.get("type") or "").lower() != "table":
            continue
        md = (b.get("text") or "").strip()
        if not md:
            continue
        cleaned_md, footnotes_raw, footnotes = _extract_footnotes(md)
        rows, cols = _measure_table(cleaned_md)
        caption, number = _find_caption(blocks, i)
        bbox = b.get("bbox")

        flags: list[str] = []
        if rows < 2:
            flags.append("very_short_table")
        if footnotes:
            flags.append("footnotes_extracted")

        out.append(
            ParsedTable(
                table_id=_table_id(doc_id, seq, cleaned_md),
                doc_id=doc_id,
                seq=seq,
                number=number,
                inferred=False,
                caption=caption,
                markdown=cleaned_md,
                n_rows=rows,
                n_cols=cols,
                page=int(b.get("page", 0)),
                block_id=b.get("id", ""),
                bbox_json=_json.dumps(bbox) if bbox else None,
                context_before=_nearest_paragraph(blocks, i, -1),
                context_after=_nearest_paragraph(blocks, i, +1),
                flags=flags,
                footnotes_raw=footnotes_raw,
                footnotes=footnotes,
            )
        )
        seq += 1
    out = _merge_multi_page_tables(out)
    _infer_numbers(out)
    return out


def _normalize_caption_for_match(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.strip().lower())


def _merge_multi_page_tables(tables: list[ParsedTable]) -> list[ParsedTable]:
    """Merge adjacent tables sharing the same caption into a single logical entry.

    Common when a wide table spans 2+ pages and Docling emits one table block per page.
    """
    if len(tables) < 2:
        return tables
    merged: list[ParsedTable] = []
    i = 0
    while i < len(tables):
        primary = tables[i]
        j = i + 1
        parts = [primary]
        while j < len(tables):
            cap_a = _normalize_caption_for_match(primary.caption)
            cap_b = _normalize_caption_for_match(tables[j].caption)
            same_caption = bool(cap_a) and cap_a == cap_b
            adjacent = tables[j].page in (primary.page, primary.page + 1, tables[j - 1].page + 1)
            if same_caption and adjacent:
                parts.append(tables[j])
                j += 1
            else:
                break
        if len(parts) > 1:
            combined_md = "\n".join(p.markdown for p in parts)
            page_first = min(p.page for p in parts)
            page_last = max(p.page for p in parts)
            flags = list(primary.flags) + [f"merged_pages_{page_first}-{page_last}"]
            footnotes: list[dict[str, str]] = []
            for p in parts:
                footnotes.extend(p.footnotes)
            merged_table = ParsedTable(
                table_id=primary.table_id,
                doc_id=primary.doc_id,
                seq=primary.seq,
                number=primary.number,
                inferred=primary.inferred,
                caption=primary.caption,
                markdown=combined_md,
                n_rows=sum(p.n_rows for p in parts),
                n_cols=primary.n_cols,
                page=page_first,
                block_id=primary.block_id,
                bbox_json=primary.bbox_json,
                context_before=primary.context_before,
                context_after=parts[-1].context_after,
                flags=flags,
                footnotes_raw=" | ".join(p.footnotes_raw or "" for p in parts if p.footnotes_raw)
                or None,
                footnotes=footnotes,
            )
            merged.append(merged_table)
        else:
            merged.append(primary)
        i = j
    # Reassign sequential seq numbers
    for k, t in enumerate(merged):
        t.seq = k
    return merged
