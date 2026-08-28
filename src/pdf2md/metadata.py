"""Rank local bibliographic evidence without hiding rejected alternatives.

Embedded fields, front-page headings, repeated headings, bookmarks, and the filename
remain distinct evidence sources. Optional GROBID enrichment runs separately.
"""

from __future__ import annotations

import re
from pathlib import Path

import pypdfium2 as pdfium

from pdf2md.schema import Block, BlockType

_DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_AUTHOR_SPLIT = re.compile(r"\s*(?:;|,|\s+\.\s+|\band\b)\s*", re.IGNORECASE)
_GENERIC_TITLES = {
    "abstract",
    "acknowledgements",
    "acknowledgments",
    "contents",
    "introduction",
    "keywords",
    "references",
    "table of contents",
    "copyright page",
    "summary of contents",
    "this page intentionally left blank",
    "title page",
}
_GENERATED_TITLE_PREFIXES = ("microsoft word -", "word -")
_GENERIC_FILENAMES = {"document", "ignored", "paper", "scan", "source", "untitled"}
_STRUCTURAL_TITLE = re.compile(
    r"^(?:part|chapter|appendix)\s+(?:\d+|[ivxlcdm]+|[a-z])\b|^\d+(?:\.\d+)*\s",
    re.IGNORECASE,
)
_FRAGMENTED_WORD = re.compile(r"\b[A-Za-z]{1,2}\s+[a-z]\s+[A-Za-z]{2,}\b")
_PLACEHOLDER_AUTHORS = {
    "admin",
    "administrator",
    "author",
    "dell",
    "unknown",
    "user",
}
# arXiv post-2007 identifier YYMM.NNNNN — the YY/MM give the submission year more
# reliably than the first 4-digit number on the page (which is often a dataset or
# citation year, e.g. "WMT 2014" on the 2017 Transformer paper).
_ARXIV = re.compile(r"(?<!\d)(\d{2})(?:0[1-9]|1[0-2])\.\d{4,5}(?!\d)")
_TAG = re.compile(r"<[^>]+>")
_CITE_AS = re.compile(
    r"\bCite as:\s*(?P<venue>.+?)\s+(?P<volume>\d+)\s*,\s*"
    r"(?P<locator>[A-Za-z]?\d+(?:[-–]\d+)?)\s*\((?P<year>(?:19|20)\d{2})\)",
    re.IGNORECASE,
)
_PUBLICATION_DATE = re.compile(
    r"\b(?P<label>submitted|accepted|published online|published|issued)\s*:\s*"
    r"(?P<date>[^•|\n]+?)(?=[ \t]*(?:[•|]|\n|$))",
    re.IGNORECASE,
)
_ISBN = re.compile(
    r"\bISBN(?:-1[03])?\s*:?[\s]*(?P<isbn>(?:97[89][\s-]?)?"
    r"\d[\d\s-]{7,15}[\dXx])\b",
    re.IGNORECASE,
)
_EDITION = re.compile(
    r"\b(?P<edition>(?:first|second|third|fourth|fifth|sixth|seventh|eighth|"
    r"ninth|tenth|\d+(?:st|nd|rd|th))\s+edition)\b",
    re.IGNORECASE,
)


def _arxiv_year(*sources: str) -> str | None:
    for s in sources:
        m = _ARXIV.search(s)
        if m and int(m.group(1)) >= 7:  # the new scheme began 2007
            return f"20{m.group(1)}"
    return None


def _title_candidate(value: str | None) -> str | None:
    title = " ".join((value or "").split())
    normalized = title.casefold().strip(" .:-")
    if not normalized or normalized in _GENERIC_TITLES:
        return None
    if normalized.startswith(_GENERATED_TITLE_PREFIXES):
        return None
    if normalized.endswith(" edition"):
        return None
    return title


def _title_penalties(title: str) -> list[str]:
    penalties = []
    tokens = title.split()
    if _FRAGMENTED_WORD.search(title) or any(
        len(token) == 1 and token.islower() for token in tokens[1:-1]
    ):
        penalties.append("probable_glyph_fragmentation")
    if _STRUCTURAL_TITLE.match(title):
        penalties.append("section_like_title")
    if len(title) < 5:
        penalties.append("too_short")
    return penalties


def _candidate_key(value: str) -> str:
    return re.sub(r"[^\w]+", " ", value.casefold()).strip()


def _add_title_evidence(
    candidates: dict[str, dict],
    rejected: list[dict],
    value: str | None,
    source: str,
    base_score: int,
    **location,
) -> None:
    title = _title_candidate(value)
    evidence = {"source": source, **location}
    if title is None:
        if value and " ".join(value.split()):
            rejected.append({
                "value": " ".join(value.split()),
                "source": source,
                "reason": "generic_or_generated_title",
                **location,
            })
        return
    key = _candidate_key(title)
    candidate = candidates.setdefault(key, {
        "value": title,
        "evidence": [],
        "base_scores": [],
        "penalties": _title_penalties(title),
    })
    candidate["evidence"].append(evidence)
    candidate["base_scores"].append(base_score)


def _ranked_title(candidate: dict) -> dict:
    source_count = len({item["source"] for item in candidate["evidence"]})
    occurrence_count = len(candidate["evidence"])
    penalty = sum({
        "probable_glyph_fragmentation": 45,
        "section_like_title": 25,
        "too_short": 20,
    }[name] for name in candidate["penalties"])
    score = (
        max(candidate["base_scores"])
        + 8 * max(0, source_count - 1)
        + min(8, 2 * max(0, occurrence_count - 1))
        - penalty
    )
    return {
        "value": candidate["value"],
        "score": score,
        "quality": "high" if score >= 90 else "medium" if score >= 65 else "low",
        "evidence": candidate["evidence"],
        "penalties": candidate["penalties"],
    }


def _filename_title(pdf_path) -> str | None:
    stem = Path(pdf_path).stem
    if stem.casefold() in _GENERIC_FILENAMES:
        return None
    words = re.sub(r"[_-]+", " ", stem)
    return _title_candidate(words)


def _title_evidence(pdf_path, blocks: list[Block], embedded: dict, bookmarks) -> dict:
    candidates: dict[str, dict] = {}
    rejected: list[dict] = []
    _add_title_evidence(
        candidates, rejected, embedded.get("Title"), "embedded_title", 90
    )

    headings = [
        block for block in blocks
        if block.type is BlockType.HEADING and block.text.strip()
    ]
    embedded_author_names = {
        _candidate_key(part)
        for part in _AUTHOR_SPLIT.split(embedded.get("Author") or "")
        if part.strip()
    }
    for block in headings:
        if block.page <= 4:
            if _candidate_key(block.text) in embedded_author_names:
                rejected.append({
                    "value": " ".join(block.text.split()),
                    "source": "front_heading",
                    "reason": "matches_embedded_author",
                    "page": block.page,
                    "block_id": block.id,
                })
                continue
            _add_title_evidence(
                candidates,
                rejected,
                block.text,
                "front_heading",
                82 - 3 * (block.page - 1),
                page=block.page,
                block_id=block.id,
            )

    filename = _filename_title(pdf_path)
    if filename:
        _add_title_evidence(
            candidates, rejected, filename, "filename", 32
        )

    repeated: dict[str, list[Block]] = {}
    for block in headings:
        repeated.setdefault(_candidate_key(block.text), []).append(block)
    for group in repeated.values():
        if (
            len(group) >= 2
            and min(block.page for block in group) <= 10
            and _candidate_key(group[0].text) in candidates
        ):
            first = group[0]
            _add_title_evidence(
                candidates,
                rejected,
                first.text,
                "repeated_heading",
                68,
                pages=sorted({block.page for block in group}),
                occurrences=len(group),
            )

    running_headers: dict[str, list[Block]] = {}
    for block in blocks:
        if block.type is BlockType.PAGE_HEADER and block.text.strip():
            running_headers.setdefault(_candidate_key(block.text), []).append(block)
    for group in running_headers.values():
        pages = sorted({block.page for block in group})
        if len(pages) >= 3:
            _add_title_evidence(
                candidates,
                rejected,
                group[0].text,
                "repeated_running_title",
                65,
                pages=pages,
                occurrences=len(group),
            )

    for title, page_index, level in bookmarks or []:
        if level == 0 and page_index < 5:
            _add_title_evidence(
                candidates,
                rejected,
                title,
                "top_level_bookmark",
                28,
                page=page_index + 1,
            )

    ranked = sorted(
        (_ranked_title(candidate) for candidate in candidates.values()),
        key=lambda item: (-item["score"], item["value"].casefold()),
    )
    return {
        "selected": ranked[0] if ranked else None,
        "alternatives": ranked[1:6],
        "rejected": rejected[:20],
    }


def _embedded_authors(embedded: dict, page_text: str) -> list[str] | None:
    author = " ".join((embedded.get("Author") or "").split())
    if not author:
        return None
    normalized = author.casefold().strip(" .")
    software_fields = {
        " ".join((embedded.get(field) or "").split()).casefold().strip(" .")
        for field in ("Creator", "Producer")
    }
    if normalized in _PLACEHOLDER_AUTHORS or normalized in software_fields:
        return None
    authors = [part for part in _AUTHOR_SPLIT.split(author) if part]
    if len(authors) == 1 and len(authors[0].split()) == 1:
        if normalized not in page_text.casefold():
            return None
    return authors or None


def _author_evidence(embedded: dict, front_text: str) -> dict:
    authors = _embedded_authors(embedded, front_text)
    raw = " ".join((embedded.get("Author") or "").split())
    if not authors:
        rejected = []
        if raw:
            rejected.append({
                "value": raw,
                "source": "embedded_author",
                "reason": "placeholder_or_uncorroborated_single_token",
            })
        return {"selected": None, "alternatives": [], "rejected": rejected}

    printed = [
        author for author in authors
        if author.casefold() in front_text.casefold()
    ]
    score = 86 + (8 if len(printed) == len(authors) else 0)
    selected = {
        "value": authors,
        "score": score,
        "quality": "high" if score >= 90 else "medium",
        "evidence": [{
            "source": "embedded_author",
            "printed_on_front_pages": printed,
        }],
        "penalties": [],
    }
    return {"selected": selected, "alternatives": [], "rejected": []}


def _printed_author_evidence(blocks: list[Block], title: str | None) -> dict:
    affiliations = next((
        index for index, block in enumerate(blocks)
        if block.page <= 4
        and block.type is BlockType.HEADING
        and block.text.strip().casefold() in {"affiliation", "affiliations"}
    ), None)
    if affiliations is None:
        return {"selected": None, "alternatives": [], "rejected": []}

    title_key = _candidate_key(title or "")
    title_indices = [
        index for index, block in enumerate(blocks[:affiliations])
        if block.type is BlockType.HEADING
        and title_key
        and _candidate_key(block.text) == title_key
    ]
    if not title_indices:
        return {"selected": None, "alternatives": [], "rejected": []}

    candidates = []
    source_blocks = []
    for block in blocks[title_indices[-1] + 1:affiliations]:
        if block.type is not BlockType.PARAGRAPH:
            continue
        text = " ".join(_TAG.sub("", block.text).split())
        if not text or re.fullmatch(r"\d+", text):
            continue
        if re.match(
            r"^(?:cite as|submitted|accepted|published|check\s*for|research article)\b",
            text,
            re.IGNORECASE,
        ):
            continue
        candidates.append(text)
        source_blocks.append(block)

    joined = " ".join(candidates)
    joined = re.sub(r"\b\d+\s*(?:,\s*[a-z]\)?)?", " ", joined, flags=re.IGNORECASE)
    joined = re.sub(r"\b[a-z]\)", " ", joined, flags=re.IGNORECASE)
    names = []
    for part in _AUTHOR_SPLIT.split(joined):
        name = " ".join(part.strip(" ,;.").split())
        words = name.split()
        if not 2 <= len(words) <= 6:
            continue
        if any(char.isdigit() for char in name):
            continue
        if not all(any(char.isalpha() for char in word) for word in words):
            continue
        names.append(name)
    names = list(dict.fromkeys(names))
    if not names:
        return {"selected": None, "alternatives": [], "rejected": []}
    return {
        "selected": {
            "value": names,
            "score": 90,
            "quality": "high",
            "evidence": [{
                "source": "printed_author_line",
                "pages": sorted({block.page for block in source_blocks}),
                "block_ids": [block.id for block in source_blocks],
                "bounded_by": "title_and_affiliations_headings",
            }],
            "penalties": [],
        },
        "alternatives": [],
        "rejected": [],
    }


def _field_evidence(value, source: str, block: Block | None = None) -> dict:
    if value is None:
        return {"selected": None, "alternatives": [], "rejected": []}
    location = ({"page": block.page, "block_id": block.id} if block else {})
    return {
        "selected": {
            "value": value,
            "quality": "high" if block else "medium",
            "evidence": [{"source": source, **location}],
            "penalties": [],
        },
        "alternatives": [],
        "rejected": [],
    }


def _valid_isbn(value: str) -> bool:
    digits = re.sub(r"[^0-9Xx]", "", value)
    if len(digits) == 10:
        total = sum(
            (10 - index) * (10 if char.upper() == "X" else int(char))
            for index, char in enumerate(digits)
        )
        return total % 11 == 0
    if len(digits) == 13 and digits.isdigit():
        total = sum(
            int(char) * (1 if index % 2 == 0 else 3)
            for index, char in enumerate(digits)
        )
        return total % 10 == 0
    return False


def _book_fields(blocks: list[Block]) -> tuple[list[str] | None, str | None, Block | None]:
    early = [block for block in blocks if block.page <= 10 and block.text.strip()]
    isbns = []
    isbn_block = None
    edition = None
    for block in early:
        for match in _ISBN.finditer(block.text):
            normalized = re.sub(r"[^0-9Xx]", "", match.group("isbn")).upper()
            if _valid_isbn(normalized):
                isbns.append(normalized)
                isbn_block = isbn_block or block
        if edition is None:
            match = _EDITION.search(block.text)
            if match:
                edition = " ".join(match.group("edition").split())
    return list(dict.fromkeys(isbns)) or None, edition, isbn_block


def extract_metadata(pdf_path, blocks: list[Block], bookmarks=None) -> dict:
    embedded = _embedded(pdf_path)
    front = [block for block in blocks if block.page <= 4]
    title_evidence = _title_evidence(pdf_path, blocks, embedded, bookmarks)
    author_evidence = _author_evidence(
        embedded, "\n".join(block.text for block in front)
    )
    title = (
        title_evidence["selected"]["value"]
        if title_evidence["selected"] else None
    )
    authors = (
        author_evidence["selected"]["value"]
        if author_evidence["selected"] else None
    )
    if not authors:
        author_evidence = _printed_author_evidence(blocks, title)
        authors = (
            author_evidence["selected"]["value"]
            if author_evidence["selected"] else None
        )

    early = [block for block in blocks if block.page <= 4]
    early_text = "\n".join(block.text for block in early)
    doi_block = next((block for block in early if _DOI.search(block.text)), None)
    doi_match = _DOI.search(doi_block.text) if doi_block else None
    citation_block = next((block for block in early if _CITE_AS.search(block.text)), None)
    citation = _CITE_AS.search(citation_block.text) if citation_block else None
    publication_dates = {
        match.group("label").casefold().replace(" ", "_"): " ".join(match.group("date").split())
        for match in _PUBLICATION_DATE.finditer(early_text)
    }
    preferred_date = publication_dates.get("published_online") or publication_dates.get("published")
    date_year = _YEAR.search(preferred_date or "")
    if date_year:
        year = date_year.group(0)
    elif citation:
        year = citation.group("year")
    else:
        early_year = _YEAR.search(early_text)
        year = early_year.group(0) if early_year else None
    # The first page-1 year is often a dataset/citation year older than publication
    # (the Transformer's "WMT 2014"). An arXiv id reveals the real year; use it only
    # to correct upward, so a journal year already on the page (RDM's 2023) stands.
    arxiv = _arxiv_year(Path(pdf_path).name, early_text)
    if arxiv and (year is None or arxiv > year):
        year = arxiv

    isbn, edition, isbn_block = _book_fields(blocks)
    venue = " ".join(citation.group("venue").split()) if citation else None
    volume = citation.group("volume") if citation else None
    locator = citation.group("locator") if citation else None

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "doi": doi_match.group(0) if doi_match else None,
        "venue": venue,
        "volume": volume,
        "citation_locator": locator,
        "publication_dates": publication_dates or None,
        "isbn": isbn,
        "edition": edition,
        "metadata_evidence": {
            "schema_version": 1,
            "method": "ranked-local-metadata-v1",
            "score_note": "Relative ranking points, not a calibrated probability.",
            "title": title_evidence,
            "authors": author_evidence,
            "year": _field_evidence(year, "front_matter_date", citation_block),
            "doi": _field_evidence(
                doi_match.group(0) if doi_match else None,
                "printed_doi",
                doi_block,
            ),
            "venue": _field_evidence(venue, "citation_line", citation_block),
            "volume": _field_evidence(volume, "citation_line", citation_block),
            "citation_locator": _field_evidence(locator, "citation_line", citation_block),
            "publication_dates": _field_evidence(
                publication_dates or None,
                "labelled_front_matter_dates",
                citation_block,
            ),
            "isbn": _field_evidence(isbn, "printed_isbn_checksum_valid", isbn_block),
            "edition": _field_evidence(edition, "printed_edition", isbn_block),
        },
    }


def _embedded(pdf_path) -> dict:
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        return pdf.get_metadata_dict() or {}
    except Exception:  # noqa: BLE001 - missing/garbled metadata is normal
        return {}
    finally:
        pdf.close()
