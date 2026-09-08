"""Who wrote it: the PDF's own Author field, or the line under the title.

Two readings with different standards, and the difference is the whole design.
A journal that prints an `Affiliations` heading bounds the author region exactly,
so everything between the title and that heading is authors and the parts that
are not names can be dropped — nothing else lives there. One corpus document in
37 prints one.

For the rest the anchor is the title, and a line chosen for its *position* cannot
be forgiving: `Department of Applied Analysis and Computer Science University of
Waterloo` is four capitalized words joined by `and`, exactly like two authors. So
`_author_names` is all-or-nothing — one part that is not a name disqualifies the
line — and it is emphatically not a name classifier. Run over the 157 title
candidates in the corpus it accepts 36, `Attention Is All You Need` among them.
"""

from __future__ import annotations

import re

from pdf2md.schema import Block, BlockType

# Shared with `metadata`: markup an engine leaves in a heading, and the
# comparison key both halves use to decide two strings name the same thing.
_TAG = re.compile(r"<[^>]+>")


def _candidate_key(value: str) -> str:
    return re.sub(r"[^\w]+", " ", value.casefold()).strip()

_AUTHOR_SPLIT = re.compile(r"\s*(?:;|,|\s+\.\s+|\band\b)\s*", re.IGNORECASE)

_PLACEHOLDER_AUTHORS = {
    "admin",
    "administrator",
    "author",
    "dell",
    "unknown",
    "user",
}

# An affiliation reads like a list of capitalized words too, and on most papers
# it is the block right under the author line.
_INSTITUTION = re.compile(
    r"\b(?:univ|universit\w*|department|dept|institut\w*|laborator\w*|college|"
    r"school|centre|center|academy|hospital|faculty|division|inc|ltd|gmbh|corp|"
    r"society|foundation|group|program|programme)\b",
    re.IGNORECASE,
)

# A person's name carries no function words; `Department of Applied Analysis` does.
# Nor the words a book's front matter puts under its title: `VOLUME I` is two
# capitalized words with nothing else to disqualify it.
_NOT_IN_A_NAME = frozenset({
    "of", "for", "the", "in", "at", "on", "by", "with", "to",
    "volume", "edition", "series", "supplement", "preface", "reprinted",
    "revised", "translated", "abridged",
})

# Lowercase words that are part of a name.
_NAME_PARTICLES = frozenset({
    "van", "von", "der", "den", "de", "del", "della", "di", "da", "dos", "du",
    "la", "le", "bin", "ibn", "al", "ter", "ten",
})

# Affiliation markers, ORCID badges and correspondence daggers ride on the line.
_AUTHOR_MARKS = re.compile(r"\biD\b|\bORCID\b|[*†‡§¶]|\b[a-z]\)|\d+")

# A journal that gives each author its own block puts a bare rule between them,
# and sometimes strands the affiliation marker in a block of its own.
_SEPARATOR_ONLY = re.compile(r"[|,;·•\d\s]+|and", re.IGNORECASE)

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

def _author_names(text: str) -> list[str] | None:
    """The people a line names, or None when it is not a list of names.

    All-or-nothing on purpose. The affiliation-bounded reading can afford to
    collect the parts that look like names and drop the rest, because the region
    it reads holds nothing else. A line chosen only for sitting under the title
    has no such guarantee, so one part that is not a name disqualifies the line:
    `Department of Applied Analysis and Computer Science University of Waterloo`
    is otherwise four capitalized words joined by `and`, exactly like two authors.
    """
    cleaned = " ".join(_TAG.sub("", text).split())
    if not cleaned or "@" in cleaned or _INSTITUTION.search(cleaned):
        return None
    names = []
    for part in _AUTHOR_SPLIT.split(_AUTHOR_MARKS.sub(" ", cleaned)):
        name = " ".join(part.strip(" ,;.").split())
        if not name:
            continue
        words = name.split()
        if not 2 <= len(words) <= 6:
            return None
        if any(word.casefold() in _NOT_IN_A_NAME for word in words):
            return None
        if not all(any(char.isalpha() for char in word) for word in words):
            return None
        if not all(
            word[0].isupper() or word.casefold() in _NAME_PARTICLES for word in words
        ):
            return None
        names.append(name)
    return list(dict.fromkeys(names)) or None

def _printed_author_evidence(blocks: list[Block], title: str | None) -> dict:
    """Authors read off the front page, when the embedded field gives none.

    Two readings, and which one applies is a property of the paper. A journal
    that prints an `Affiliations` heading bounds the author region exactly, and
    everything between the title and that heading is authors: strong evidence,
    and the parts that are not names can be dropped because nothing else lives
    there. Most papers print no such heading. For those the anchor is the title
    itself -- across the corpus the author line is the block directly under it --
    and the line must parse as nothing but names, because the block below the
    title is the affiliation just as often."""
    title_key = _candidate_key(title or "")
    if not title_key:
        return _no_author_evidence()
    title_indices = [
        index for index, block in enumerate(blocks)
        if block.page <= 4
        and block.type is BlockType.HEADING
        and _candidate_key(block.text) == title_key
    ]
    if not title_indices:
        return _no_author_evidence()
    affiliations = next((
        index for index, block in enumerate(blocks)
        if block.page <= 4
        and block.type is BlockType.HEADING
        and block.text.strip().casefold() in {"affiliation", "affiliations"}
        and index > title_indices[0]
    ), None)
    if affiliations is not None:
        return _authors_between(blocks, title_indices, affiliations)
    return _authors_under_title(blocks, title_indices[0])

def _no_author_evidence() -> dict:
    return {"selected": None, "alternatives": [], "rejected": []}

def _authors_under_title(blocks: list[Block], title_index: int) -> dict:
    """The run of name blocks directly below the title.

    One block is not always the whole list: a journal that puts each author in
    its own block with a `|` between them yields five blocks, and answering with
    the first would be a wrong author list rather than a partial one. The run
    must start at the first block of text under the title and ends at the first
    block that is neither names nor a separator -- normally the affiliation."""
    names: list[str] = []
    used: list[Block] = []
    for block in blocks[title_index + 1:]:
        text = " ".join(_TAG.sub("", block.text).split())
        if not text:
            continue
        if block.page > 4:
            break
        if _SEPARATOR_ONLY.fullmatch(text):
            continue
        found = _author_names(text)
        if not found:
            break
        names += found
        used.append(block)
    names = list(dict.fromkeys(names))
    if not names:
        return _no_author_evidence()
    return {
        "selected": {
            "value": names,
            "score": 78,
            "quality": "medium",
            "evidence": [{
                "source": "printed_author_line",
                "pages": sorted({block.page for block in used}),
                "block_ids": [block.id for block in used],
                "bounded_by": "block_under_title",
            }],
            "penalties": [],
        },
        "alternatives": [],
        "rejected": [],
    }

def _authors_between(blocks: list[Block], title_indices: list[int],
                     affiliations: int) -> dict:
    inside = [index for index in title_indices if index < affiliations]
    if not inside:
        return _no_author_evidence()
    candidates = []
    source_blocks = []
    for block in blocks[inside[-1] + 1:affiliations]:
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
        return _no_author_evidence()
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
