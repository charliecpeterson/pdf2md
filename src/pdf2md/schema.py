"""Core data model for pdf2md.

Dataclasses + asdict everywhere; no Pydantic. A `Document` owns a recursive
`Section` tree plus flat `Block`/`TableData`/`FigureRef` lists referenced by id,
so the tree and the block inventory stay independently walkable. `provenance.json`
on disk is the serialized source of truth; the `.md`/`assets` output is derived.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

# Bumped when the on-disk output contract changes in a way that would break a
# naive downstream parser (front-matter keys removed/renamed, file layout shift).
FORMAT_VERSION = "0.1"


class BlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    FIGURE = "figure"
    EQUATION = "equation"
    CODE = "code"
    CAPTION = "caption"
    FOOTNOTE = "footnote"
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"
    OTHER = "other"


class CoverageStatus(str, Enum):
    """How a block was accounted for, set by the coverage auditor."""

    PENDING = "pending"      # not yet audited
    EMITTED = "emitted"      # rendered as text/table/equation in the markdown
    CROPPED = "cropped"      # represented as a referenced image crop
    FLAGGED = "flagged"      # emitted but low-confidence; visible marker added
    DROPPED = "dropped"      # could not be represented; visible marker added


class SectionKind(str, Enum):
    FRONT_MATTER = "front_matter"
    PART = "part"
    CHAPTER = "chapter"
    SECTION = "section"
    APPENDIX = "appendix"


@dataclass
class BBox:
    """Region in PDF point coordinates (bottom-left origin, as Docling emits)."""

    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Block:
    id: str
    type: BlockType
    text: str
    page: int
    bbox: BBox | None = None
    confidence: float | None = None
    engine: str = "docling"
    coverage_status: CoverageStatus = CoverageStatus.PENDING
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class TableData:
    block_id: str
    page: int
    bbox: BBox | None
    gfm: str
    html: str | None = None
    has_spanning_cells: bool = False


@dataclass
class FigureRef:
    block_id: str
    page: int
    bbox: BBox | None
    caption: str | None = None
    asset_path: str = ""  # relative path under the version dir, set by render


@dataclass
class Section:
    id: str
    title: str
    depth: int
    kind: SectionKind
    page_start: int
    block_ids: list[str] = field(default_factory=list)
    children: list["Section"] = field(default_factory=list)


@dataclass
class Provenance:
    tool_version: str
    engine_versions: dict[str, str]
    format_version: str
    source_path: str
    source_sha256: str
    page_count: int
    started_at: str
    finished_at: str
    duration_s: float
    section_source: str  # "bookmarks" | "heading_outline" | "none"


@dataclass
class CoverageFlag:
    block_id: str
    page: int
    reason: str
    marker_text: str


@dataclass
class CoverageReport:
    doc_id: str
    total_blocks: int
    emitted: int
    cropped: int
    flagged: int
    dropped: int
    flags: list[CoverageFlag] = field(default_factory=list)

    @property
    def lossless(self) -> bool:
        """True when no block was dropped without a visible marker."""
        return self.total_blocks == self.emitted + self.cropped + self.flagged + self.dropped


@dataclass
class Document:
    doc_id: str
    source_path: str
    source_sha256: str
    version: int
    page_count: int
    sections: Section
    blocks: list[Block] = field(default_factory=list)
    tables: list[TableData] = field(default_factory=list)
    figures: list[FigureRef] = field(default_factory=list)
    provenance: Provenance | None = None
    coverage: CoverageReport | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
