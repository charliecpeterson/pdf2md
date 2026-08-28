"""Split passage text on content boundaries while enforcing a token budget."""

from __future__ import annotations

import re
from collections.abc import Callable

from pdf2md.passage_tokenizer import PassageTokenizer
from pdf2md.schema import BlockType


_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")
_GFM_SEPARATOR = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)


def _fits(
    text: str,
    contextualize: Callable[[str], str],
    tokenizer: PassageTokenizer,
    max_tokens: int,
) -> bool:
    return tokenizer.count(contextualize(text)) <= max_tokens


def _preferred_cut(text: str, upper: int) -> int:
    lower = max(1, upper // 2)
    sentence_cuts = [
        match.end()
        for match in re.finditer(r"(?<=[.!?])\s+", text[: upper + 1])
        if match.end() >= lower
    ]
    if sentence_cuts:
        return sentence_cuts[-1]
    for boundary in ("\n\n", "\n", " "):
        cut = text.rfind(boundary, lower, upper + 1)
        if cut >= lower:
            return cut + (1 if boundary == " " else 0)
    return upper


def _split_oversized(
    text: str,
    contextualize: Callable[[str], str],
    tokenizer: PassageTokenizer,
    max_tokens: int,
) -> list[str]:
    remaining = text.strip()
    parts: list[str] = []
    while remaining and not _fits(remaining, contextualize, tokenizer, max_tokens):
        low, high = 1, len(remaining)
        best = 0
        while low <= high:
            middle = (low + high) // 2
            if _fits(remaining[:middle], contextualize, tokenizer, max_tokens):
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        if best == 0:
            raise ValueError(
                "passage context alone exceeds passage_max_tokens; raise the limit "
                "or shorten the document metadata"
            )
        cut = _preferred_cut(remaining, best)
        if not _fits(remaining[:cut].strip(), contextualize, tokenizer, max_tokens):
            cut = best
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def _pack_units(
    units: list[str],
    contextualize: Callable[[str], str],
    tokenizer: PassageTokenizer,
    max_tokens: int,
    *,
    separator: str,
) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    for unit in units:
        candidate = separator.join([*current, unit])
        if _fits(candidate, contextualize, tokenizer, max_tokens):
            current.append(unit)
            continue
        if current:
            parts.append(separator.join(current))
            current = []
        if _fits(unit, contextualize, tokenizer, max_tokens):
            current = [unit]
        else:
            parts.extend(
                _split_oversized(unit, contextualize, tokenizer, max_tokens)
            )
    if current:
        parts.append(separator.join(current))
    return parts


def prose_units(text: str) -> list[str]:
    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        sentences = [part.strip() for part in _SENTENCE_BOUNDARY.split(paragraph)]
        units.extend(part for part in sentences if part)
    return units


def _split_table(
    text: str,
    contextualize: Callable[[str], str],
    tokenizer: PassageTokenizer,
    max_tokens: int,
) -> list[str] | None:
    lines = [line.rstrip() for line in text.strip().splitlines()]
    separator_index = next(
        (index for index, line in enumerate(lines) if _GFM_SEPARATOR.match(line)),
        None,
    )
    if separator_index is None or separator_index == 0:
        return None

    header = "\n".join(lines[: separator_index + 1])
    rows = [line for line in lines[separator_index + 1 :] if line.strip()]
    if not rows:
        return [text.strip()]

    def with_header(body: str) -> str:
        return f"{header}\n{body}" if body else header

    if not _fits(header, contextualize, tokenizer, max_tokens):
        raise ValueError(
            "table header exceeds passage_max_tokens and cannot be repeated safely"
        )

    parts: list[str] = []
    current: list[str] = []
    for row in rows:
        candidate = with_header("\n".join([*current, row]))
        if _fits(candidate, contextualize, tokenizer, max_tokens):
            current.append(row)
            continue
        if current:
            parts.append(with_header("\n".join(current)))
            current = []
        if _fits(with_header(row), contextualize, tokenizer, max_tokens):
            current = [row]
            continue

        row_parts = _split_oversized(
            row,
            lambda part: contextualize(with_header(part)),
            tokenizer,
            max_tokens,
        )
        parts.extend(with_header(part) for part in row_parts)
    if current:
        parts.append(with_header("\n".join(current)))
    return parts


def split_passage_text(
    text: str,
    content_type: BlockType,
    contextualize: Callable[[str], str],
    tokenizer: PassageTokenizer,
    max_tokens: int,
) -> list[str]:
    if max_tokens < 1:
        raise ValueError("passage_max_tokens must be positive")
    text = text.strip()
    if not text:
        return []
    if _fits(text, contextualize, tokenizer, max_tokens):
        return [text]

    if content_type is BlockType.TABLE:
        table_parts = _split_table(text, contextualize, tokenizer, max_tokens)
        if table_parts is not None:
            return table_parts

    if content_type in {BlockType.LIST, BlockType.CODE}:
        units = [line for line in text.splitlines() if line.strip()]
        return _pack_units(
            units, contextualize, tokenizer, max_tokens, separator="\n"
        )

    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", text)
        if paragraph.strip()
    ]
    return _pack_units(
        paragraphs, contextualize, tokenizer, max_tokens, separator="\n\n"
    )
