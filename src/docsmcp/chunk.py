from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from docsmcp.postprocess.dedup import dedup_repeats


_WORD = re.compile(r"\S+")

DEFAULT_TARGET_TOKENS = 500
DEFAULT_OVERLAP_BLOCKS = 1


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    seq: int
    text: str
    token_count: int
    page_first: int
    page_last: int
    block_ids: list[str]
    section: str | None = None
    prev_chunk_id: str | None = None
    next_chunk_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _token_count(text: str) -> int:
    return len(_WORD.findall(text))


def _chunk_id(doc_id: str, seq: int, text: str) -> str:
    h = hashlib.sha1(f"{doc_id}:{seq}:{text[:200]}".encode()).hexdigest()
    return f"c{h[:12]}"


def _is_heading(block: dict[str, Any]) -> bool:
    return block.get("type") == "heading"


def _is_skippable(block: dict[str, Any]) -> bool:
    """Skip blocks that don't carry searchable content."""
    if block.get("type") == "figure" and not (block.get("text") or "").strip():
        return True
    text = (block.get("text") or "").strip()
    if not text:
        return True
    if text in {"<!-- image -->", "-----"}:
        return True
    return False


def chunk_blocks(
    doc_id: str,
    blocks: list[dict[str, Any]],
    *,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_blocks: int = DEFAULT_OVERLAP_BLOCKS,
) -> list[Chunk]:
    """Build chunks from a doc's blocks.

    Rules:
    - Accumulate consecutive blocks until token budget is reached.
    - Never cross a heading boundary mid-chunk; a new heading starts a fresh chunk.
    - On chunk break, replay the last `overlap_blocks` blocks into the next chunk for context.
    - Each chunk records all source block ids and the page range it spans.
    """
    chunks: list[Chunk] = []
    cur: list[dict[str, Any]] = []
    cur_tokens = 0
    current_section: str | None = None
    chunk_open_section: str | None = None
    seq = 0

    def emit() -> None:
        nonlocal cur, cur_tokens, seq, chunk_open_section
        if not cur:
            return
        text_parts = [b["text"] for b in cur if (b.get("text") or "").strip()]
        text = "\n\n".join(text_parts).strip()
        text = dedup_repeats(text)
        if not text:
            cur = []
            cur_tokens = 0
            return
        pages = [int(b.get("page", 0)) for b in cur if b.get("page")]
        block_ids = [b.get("id", "") for b in cur]
        # Prefer the first heading in this chunk; fall back to section at chunk start.
        section = chunk_open_section
        for b in cur:
            if _is_heading(b):
                heading_text = (b.get("text") or "").strip()
                if heading_text:
                    section = heading_text
                    break
        chunk_id = _chunk_id(doc_id, seq, text)
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                doc_id=doc_id,
                seq=seq,
                text=text,
                token_count=_token_count(text),
                page_first=min(pages) if pages else 0,
                page_last=max(pages) if pages else 0,
                block_ids=block_ids,
                section=section,
            )
        )
        seq += 1
        if overlap_blocks > 0 and len(cur) > overlap_blocks:
            tail = cur[-overlap_blocks:]
            cur = list(tail)
            cur_tokens = sum(_token_count(b.get("text") or "") for b in cur)
        else:
            cur = []
            cur_tokens = 0
        chunk_open_section = current_section

    for block in blocks:
        if _is_skippable(block):
            continue
        if _is_heading(block):
            emit()
            current_section = (block.get("text") or "").strip() or current_section
            chunk_open_section = current_section
            cur.append(block)
            cur_tokens = _token_count(block.get("text") or "")
            continue

        btokens = _token_count(block.get("text") or "")
        if cur_tokens + btokens > target_tokens and cur:
            emit()
        if not cur:
            chunk_open_section = current_section
        cur.append(block)
        cur_tokens += btokens

    emit()

    for i, c in enumerate(chunks):
        if i > 0:
            c.prev_chunk_id = chunks[i - 1].chunk_id
        if i + 1 < len(chunks):
            c.next_chunk_id = chunks[i + 1].chunk_id

    return chunks
