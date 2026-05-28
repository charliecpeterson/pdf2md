from __future__ import annotations

import hashlib
import json as _json
import re
from dataclasses import dataclass, field
from typing import Any


_FIGURE_LABEL_RE = re.compile(
    r"(?i)\b(?:Figure|Fig\.?)\s*"
    r"([IVXLCDM]+|[0-9]+(?:\.(?:[0-9]+|[IVXLCDM]+))*[a-z]?)"
    r"[\s.:(]"
)
_NEIGHBOR_WINDOW = 3


_PANEL_LABEL = re.compile(r"\(([a-z])\)\s+([^()]{2,}?)(?=\s+\([a-z]\)\s|\s*[.;]\s*$|\s*$)")


def parse_panels(caption: str) -> list[dict[str, str]]:
    """Extract '(a) ... (b) ... (c) ...' style sub-figure labels from a caption.

    Returns a list of {label, description}. Empty list if no panels found.
    """
    if not caption:
        return []
    matches = _PANEL_LABEL.findall(caption)
    if not matches:
        return []
    out: list[dict[str, str]] = []
    for label, desc in matches:
        out.append({"label": label, "description": desc.strip().rstrip(",;.")})
    return out


@dataclass
class ParsedFigure:
    figure_id: str
    doc_id: str
    seq: int
    number: str | None
    inferred: bool
    caption: str | None
    page: int
    block_id: str
    bbox_json: str | None
    context_before: str | None = None
    context_after: str | None = None
    flags: list[str] = field(default_factory=list)
    panels: list[dict[str, str]] = field(default_factory=list)


def _fig_id(doc_id: str, seq: int, caption: str) -> str:
    return "f" + hashlib.sha1(f"{doc_id}:{seq}:{(caption or '')[:200]}".encode()).hexdigest()[:12]


def _find_caption(blocks: list[dict[str, Any]], i: int) -> tuple[str | None, str | None]:
    from docsmcp.postprocess.roman import normalize_label_number

    n = len(blocks)
    for delta in range(1, _NEIGHBOR_WINDOW + 1):
        for j in (i + delta, i - delta):
            if 0 <= j < n:
                text = (blocks[j].get("text") or "").strip()
                if not text:
                    continue
                m = _FIGURE_LABEL_RE.match(text)
                if m:
                    return text, normalize_label_number(m.group(1))
    return None, None


def _nearest_paragraph(blocks: list[dict[str, Any]], i: int, direction: int) -> str | None:
    n = len(blocks)
    step = 1 if direction > 0 else -1
    j = i + step
    while 0 <= j < n:
        b = blocks[j]
        if (b.get("type") or "") == "paragraph" and (b.get("text") or "").strip():
            return (b["text"] or "").strip()
        j += step
    return None


def _infer_numbers(figures: list[ParsedFigure]) -> None:
    """Fill in missing figure numbers from neighbors. Lenient anchor parsing for
    decimal-form labels like '1.1' or 'A.3'."""
    from docsmcp.postprocess.inference import infer_numbers_table

    infer_numbers_table(figures)


def parse_figures(doc_id: str, blocks: list[dict[str, Any]]) -> list[ParsedFigure]:
    """Extract real captioned figures from a doc, skipping uncaptioned decorative blocks
    (ACS journal-template icons, graphical-abstract elements, page-corner ornaments)."""
    seen_captions: set[str] = set()
    out: list[ParsedFigure] = []
    seq = 0
    for i, b in enumerate(blocks):
        if (b.get("type") or "").lower() != "figure":
            continue
        caption, number = _find_caption(blocks, i)
        if not caption:
            continue
        if caption in seen_captions:
            continue
        seen_captions.add(caption)
        bbox = b.get("bbox")
        out.append(
            ParsedFigure(
                figure_id=_fig_id(doc_id, seq, caption),
                doc_id=doc_id,
                seq=seq,
                number=number,
                inferred=False,
                caption=caption,
                page=int(b.get("page", 0)),
                block_id=b.get("id", ""),
                bbox_json=_json.dumps(bbox) if bbox else None,
                context_before=_nearest_paragraph(blocks, i, -1),
                context_after=_nearest_paragraph(blocks, i, +1),
                panels=parse_panels(caption),
            )
        )
        seq += 1
    _infer_numbers(out)
    return out
