from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


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
    OTHER = "other"


class VerifyStatus(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    DISAGREEMENT = "disagreement"
    FLAGGED = "flagged"


class PageClass(str, Enum):
    BORN_DIGITAL = "born_digital"
    MIXED = "mixed"
    SCANNED = "scanned"
    EMPTY = "empty"


@dataclass
class PageTriage:
    page: int
    char_count: int
    image_count: int
    width: float
    height: float
    classification: PageClass


@dataclass
class BBox:
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
    verify: VerifyStatus = VerifyStatus.UNVERIFIED
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class BuildInfo:
    tool_version: str
    engine_versions: dict[str, str]
    profile: str
    started_at: str
    finished_at: str
    duration_s: float


@dataclass
class Document:
    doc_id: str
    source_path: str
    source_sha256: str
    version: int
    page_count: int
    blocks: list[Block]
    build: BuildInfo
    pages: list[PageTriage] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
