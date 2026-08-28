"""Index explicitly defined technical symbols without inferring their meaning.

Each entry remains local to its deepest section and quotes the defining sentence.
The index declines symbols that appear without an explicit definition pattern.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from pdf2md.passage_split import prose_units


SYMBOL_INDEX_SCHEMA_VERSION = 1
_INLINE_MATH = re.compile(r"(?<!\$)\$([^$\n]{1,40})\$(?!\$)")
_DISPLAY_MATH = re.compile(r"\$\$(.*?)\$\$", re.DOTALL)
_CONTEXT_PREFIXES = (
    "Explanatory context: ",
    "Referring text: ",
    "Table caption: ",
)
_SYMBOL = r"(?:\$([^$\n]{1,40})\$|\b([A-Za-z](?:_[A-Za-z0-9{}]+)?))"
_DEFINITION_PATTERNS = (
    re.compile(
        rf"\b(?:where|with)\s+{_SYMBOL}\s+(?:is|are|denotes?|represents?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?<![\w$]){_SYMBOL}\s+(?:is|are)\s+(?:defined|introduced)\s+as\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:let|define)\s+{_SYMBOL}\s+(?:be|as)\b",
        re.IGNORECASE,
    ),
)


def _normalized_symbol(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _source_quote(sentence: str) -> str:
    for prefix in _CONTEXT_PREFIXES:
        if sentence.startswith(prefix):
            return sentence[len(prefix):]
    return sentence


def _defined_symbols(sentence: str) -> list[str]:
    found = []
    for pattern in _DEFINITION_PATTERNS:
        for match in pattern.finditer(sentence):
            symbol = _normalized_symbol(match.group(1) or match.group(2) or "")
            if (
                symbol
                and "=" not in symbol
                and "," not in symbol
                and len(symbol.split()) <= 3
                and symbol not in found
            ):
                found.append(symbol)
    return found


def _math_fragments(text: str) -> list[str]:
    return [
        *(_normalized_symbol(match) for match in _INLINE_MATH.findall(text)),
        *(_normalized_symbol(match) for match in _DISPLAY_MATH.findall(text)),
    ]


def _contains_symbol(text: str, symbol: str) -> bool:
    if any(symbol in fragment for fragment in _math_fragments(text)):
        return True
    if len(symbol) == 1 and symbol.isalnum():
        return bool(re.search(rf"\b{re.escape(symbol)}\b", text))
    return symbol in text


def _scope(passage: dict) -> dict:
    breadcrumb = passage.get("section_breadcrumb", [])
    if not breadcrumb:
        return {"section_id": "root", "section_title": ""}
    section = breadcrumb[-1]
    return {
        "section_id": section["id"],
        "section_title": section.get("title", ""),
    }


def _entry_id(symbol: str, section_id: str, quote: str) -> str:
    key = "\0".join((symbol, section_id, quote))
    return "symbol-" + hashlib.sha256(key.encode()).hexdigest()[:20]


def build_symbol_index(document_id: str, passages: list[dict]) -> dict:
    by_scope: dict[str, list[dict]] = {}
    for passage in passages:
        by_scope.setdefault(_scope(passage)["section_id"], []).append(passage)

    entries = []
    seen = set()
    for passage in passages:
        if not set(passage["content_types"]).intersection({"paragraph", "list", "caption"}):
            continue
        scope = _scope(passage)
        for sentence in prose_units(passage["display_text"]):
            quote = _source_quote(sentence)
            for symbol in _defined_symbols(quote):
                key = (symbol, scope["section_id"], quote)
                if key in seen:
                    continue
                seen.add(key)
                occurrences = []
                for candidate in by_scope[scope["section_id"]]:
                    if not _contains_symbol(candidate["display_text"], symbol):
                        continue
                    occurrences.append({
                        "passage_id": candidate["id"],
                        "markdown": candidate["markdown"],
                        "pages": sorted({
                            source["page"] for source in candidate["sources"]
                        }),
                    })
                entries.append({
                    "id": _entry_id(symbol, scope["section_id"], quote),
                    "symbol": symbol,
                    "scope": scope,
                    "meaning_status": "source_quoted",
                    "definition": {
                        "quote": quote,
                        "passage_id": passage["id"],
                        "markdown": passage["markdown"],
                        "sources": passage["sources"],
                    },
                    "occurrences": occurrences,
                })

    entries.sort(key=lambda entry: (
        entry["symbol"].casefold(),
        entry["scope"]["section_id"],
        entry["definition"]["passage_id"],
    ))
    return {
        "schema_version": SYMBOL_INDEX_SCHEMA_VERSION,
        "document_id": document_id,
        "method": {
            "name": "explicit-local-definitions-v1",
            "inference": "none",
            "scope": "deepest section breadcrumb",
            "note": (
                "Only explicit definition phrases create entries. The quoted source "
                "sentence is authoritative; identical symbols in other sections remain separate."
            ),
        },
        "entry_count": len(entries),
        "entries": entries,
    }


def write_symbol_index(version_dir: Path, document_id: str, passages_path: Path) -> Path:
    passages = [
        json.loads(line)
        for line in passages_path.read_text().splitlines()
        if line.strip()
    ]
    path = version_dir / "symbols.json"
    path.write_text(json.dumps(build_symbol_index(document_id, passages), indent=2) + "\n")
    return path
