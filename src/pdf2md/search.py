"""Search completed passage bundles while preserving source and review context.

The search is deliberately literal and offline. It reads the stable passage
interface instead of inventing a second corpus index or retrieval contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from pdf2md.cache import (
    content_hash,
    doc_dir,
    document_dirs,
    is_document_dir,
    latest_version,
)


@dataclass(frozen=True)
class SearchMatch:
    version_dir: Path
    passage_id: str
    title: str
    page: int
    section: str
    authority: str
    review_dispositions: tuple[str, ...]
    excerpt: str
    source: str


def _latest_bundle(document: Path) -> Path:
    version = latest_version(document)
    if version is None:
        raise ValueError(f"no completed conversion found under {document}")
    bundle = document / f"v{version}"
    if not (bundle / "passages.jsonl").is_file():
        raise ValueError(f"completed bundle has no passages.jsonl: {bundle}")
    return bundle


def search_bundles(target: Path, *, output_root: Path | None = None) -> list[Path]:
    candidate = Path(target).expanduser().resolve()
    if candidate.is_file() and candidate.name == "passages.jsonl":
        return [candidate.parent]
    if candidate.is_file() and candidate.suffix.casefold() == ".pdf":
        document = doc_dir(
            content_hash(candidate),
            candidate,
            root=output_root,
        )
        return [_latest_bundle(document)]
    if candidate.is_dir() and (candidate / "provenance.json").is_file():
        if not (candidate / "passages.jsonl").is_file():
            raise ValueError(f"completed bundle has no passages.jsonl: {candidate}")
        return [candidate]
    if candidate.is_dir() and is_document_dir(candidate):
        return [_latest_bundle(candidate)]
    if candidate.is_dir():
        bundles = []
        for document in document_dirs(candidate, recursive=True):
            version = latest_version(document)
            if version is None:
                continue
            bundle = document / f"v{version}"
            if (bundle / "passages.jsonl").is_file():
                bundles.append(bundle)
        if bundles:
            return bundles
        raise ValueError(f"no completed bundles with passages.jsonl found under {candidate}")
    raise ValueError(
        "target must be a source PDF, document directory, completed bundle, "
        "passages.jsonl, or output library"
    )


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _excerpt(text: str, query: str, *, max_chars: int = 360) -> str:
    normalized = _normalized(text)
    if len(normalized) <= max_chars:
        return normalized
    position = normalized.casefold().find(query.casefold())
    context = max(0, (max_chars - len(query)) // 2)
    start = max(0, position - context)
    end = min(len(normalized), start + max_chars)
    if end - start < max_chars:
        start = max(0, end - max_chars)
    if start:
        boundary = normalized.find(" ", start)
        start = boundary + 1 if boundary >= 0 else start
    if end < len(normalized):
        boundary = normalized.rfind(" ", start, end)
        end = boundary if boundary >= 0 else end
    excerpt = normalized[start:end].strip()
    return ("…" if start else "") + excerpt + ("…" if end < len(normalized) else "")


def _primary_source(passage: dict) -> dict:
    sources = passage.get("sources") or []
    primary = next(
        (source for source in sources if source.get("role") == "primary"),
        None,
    )
    if primary is None:
        raise ValueError(f"passage has no source: {passage.get('id', '<unknown>')}")
    return primary


def _source_link(version_dir: Path, source: dict) -> str:
    reference = source.get("source_page") or f"../source.pdf#page={source['page']}"
    relative, separator, fragment = reference.partition("#")
    resolved = (version_dir / relative).resolve()
    return str(resolved) + (f"#{fragment}" if separator else "")


def _load_passages(version_dir: Path) -> list[dict]:
    path = version_dir / "passages.jsonl"
    records = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in {path}:{line_number}: {exc.msg}") from exc
    return records


def find_passages(
    target: Path,
    query: str,
    *,
    output_root: Path | None = None,
    limit: int = 20,
) -> list[SearchMatch]:
    needle = _normalized(query)
    if not needle:
        raise ValueError("query must contain non-whitespace text")
    if limit < 1:
        raise ValueError("limit must be positive")

    matches = []
    for version_dir in search_bundles(target, output_root=output_root):
        for passage in _load_passages(version_dir):
            display_text = passage.get("display_text") or ""
            if needle.casefold() not in _normalized(display_text).casefold():
                continue
            source = _primary_source(passage)
            breadcrumb = passage.get("section_breadcrumb") or []
            document = passage.get("document") or {}
            review = passage.get("review") or {}
            matches.append(SearchMatch(
                version_dir=version_dir,
                passage_id=str(passage.get("id") or ""),
                title=str(document.get("title") or version_dir.parent.name),
                page=int(source["page"]),
                section=str(breadcrumb[-1].get("title") or "") if breadcrumb else "",
                authority=str(passage.get("authority") or "unknown"),
                review_dispositions=tuple(review.get("dispositions") or ()),
                excerpt=_excerpt(display_text, needle),
                source=_source_link(version_dir, source),
            ))
            if len(matches) == limit:
                return matches
    return matches
