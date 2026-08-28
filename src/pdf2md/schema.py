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
# 0.11: books selectively expand Part-like bookmark containers into chapter files,
# retain each opener, and keep detailed headings in file-local contents.
FORMAT_VERSION = "0.11"


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


# The text-bearing block types — held to ligature/script/refill repair, the legibility
# gate, VLM re-OCR, and the prose-legibility tally. One definition so the four call
# sites can't drift (they had, on FOOTNOTE).
PROSE_TYPES = frozenset({
    BlockType.PARAGRAPH, BlockType.HEADING, BlockType.LIST,
    BlockType.CAPTION, BlockType.FOOTNOTE, BlockType.OTHER,
})


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
    # Set when the "table" is really an ASCII-art / console block the engine can't
    # grid: the line-preserved text, emitted as a code fence instead of a GFM table.
    preformatted: str | None = None
    # Structured artifacts written during emission. Scanned-page values remain OCR
    # candidates; these paths make them searchable and inspectable without promoting
    # them over the authoritative source crop.
    candidate_path: str = ""
    data_path: str = ""
    json_path: str = ""
    normalized_data_path: str = ""
    normalized_json_path: str = ""
    cell_evidence_path: str = ""
    cell_evidence_counts: dict[str, int] = field(default_factory=dict)
    cell_resolution_counts: dict[str, int] = field(default_factory=dict)
    # Born-digital glyph verification (table_rebuild.check_table_cells): per-cell
    # verdict counts plus uncovered-ink and bounded mismatch samples. Read-only
    # evidence; a mismatch flags review, it never rewrites the cell.
    cell_glyph_check: dict[str, Any] = field(default_factory=dict)


@dataclass
class RawCell:
    """A table cell as the engine translated it: text cleaned but not yet
    ligature-repaired or script-annotated, plus the bbox `enrich` needs to recover
    inline sub/superscripts from glyph geometry."""
    text: str
    bbox: BBox | None
    row: int
    col: int
    row_span: int
    col_span: int
    header: bool


@dataclass
class RawTable:
    """The engine's structured translation of a table, handed to `enrich` so the
    religature + script rebuild happens engine-agnostically. Transient (rides on
    `EngineResult`, never serialized)."""
    cells: list[RawCell]
    num_rows: int
    num_cols: int


@dataclass
class Digitization:
    """Data recovered from a chart figure. `series` is one list of (x, y) data points per
    curve; `confidence` (0..1) and `note` say how well the axes could be calibrated, so a
    reader knows how far to trust the numbers. See digitize.py."""

    series: list[list[tuple[float, float]]]
    method: str
    confidence: float
    note: str
    verify_asset: str | None = None  # round-trip composite (original vs reconstruction), tier-2
    # How each axis maps position to value ("linear"/"log"), so the recovered numbers are
    # unambiguous and the repro script sets the right scale. (Axis titles are not carried
    # here: reading them off the plot geometry is unreliable for rotated matplotlib labels;
    # the printed titles come through --figure-labels instead.)
    x_kind: str = "linear"
    y_kind: str = "linear"
    kind: str = "line"  # what the series are: "line" | "scatter" | "bar" — drives the repro script
    # Per-series labels, parallel to `series` — set for multi-panel figures ("panel 2
    # series 1"), None for the common single-panel case.
    series_names: list[str] | None = None
    # Multi-sample consensus (--digitize-consensus): how many VLM reads the curve is
    # the median of (0 = single read), and the mean per-bin dispersion across reads
    # as a fraction of the y-range — the uncertainty signal that scales confidence.
    consensus_votes: int = 0
    dispersion: float | None = None


@dataclass
class FigureLabels:
    """Printed text read off a figure (--figure-labels) via the OCR model: axis titles,
    peak/data labels, legend, numeric markers. Reliable for printed text where curve
    digitization can't be, but a model read (OCR), so confidence < 1 and the crop stays
    authoritative."""

    text: str
    confidence: float
    note: str


@dataclass
class FigureRef:
    block_id: str
    page: int
    bbox: BBox | None
    caption: str | None = None
    caption_bbox: BBox | None = None  # the caption text's own bbox, for font-decode refill
    asset_path: str = ""  # relative path under the version dir, set by render
    svg_path: str = ""  # lossless vector export of the region (--figure-svg), when it succeeded
    data_path: str = ""  # accepted chart data under data/, set by emit
    code_path: str = ""  # deterministic chart reproduction script under code/, set by emit
    description: str | None = None  # optional VLM description of the crop (--describe)
    digitization: Digitization | None = None  # recovered chart data (--digitize)
    data_extraction_status: str = "not_attempted"
    data_extraction_note: str = ""
    labels: FigureLabels | None = None  # printed text read off the figure (--figure-labels)


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
    derivation: dict[str, Any] = field(default_factory=lambda: {"kind": "base"})
    run_fingerprint: str = ""
    run_inputs: dict[str, Any] = field(default_factory=dict)
    run_metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class CoverageFlag:
    block_id: str
    page: int
    reason: str
    marker_text: str
    disposition: str = "action_required"
    severity: str = "medium"
    content_impact: str = "medium"


@dataclass
class CoverageReport:
    doc_id: str
    total_blocks: int
    emitted: int
    cropped: int
    flagged: int
    dropped: int
    illegible: int = 0  # prose blocks whose text stayed symbol-font garbage (a subset of flagged)
    flags: list[CoverageFlag] = field(default_factory=list)

    @property
    def accounted_for(self) -> bool:
        """Whether every block detected by the engine received a disposition."""
        return self.total_blocks == self.emitted + self.cropped + self.flagged + self.dropped

    @property
    def complete(self) -> bool:
        """Whether every detected block has a resolved text or image representation."""
        return self.accounted_for and self.flagged == 0 and self.dropped == 0

    @property
    def needs_review(self) -> bool:
        return any(flag.disposition == "action_required" for flag in self.flags) or not self.complete

    @property
    def lossless(self) -> bool:
        """Compatibility alias for the old accounting signal."""
        return self.accounted_for


@dataclass
class DocumentProfile:
    """A per-document inventory and evidence-backed quality portrait."""
    pages: int
    blocks: int
    by_type: dict[str, int]            # block-type value -> count
    figures: int
    tables: int
    tables_verified: int                 # structured table data accepted as the readable record
    tables_candidates: int               # structured OCR retained beside an authoritative crop
    tables_image_only: int               # no structured candidate was recovered
    derived_table_datasets: int          # normalized datasets derived from source table blocks
    table_cell_evidence: dict[str, int]  # per-cell verification status counts
    table_cell_resolution: dict[str, int]  # per-cell consumer confidence counts
    table_cell_glyph_check: dict[str, int]  # born-digital glyph verdicts (+ uncovered ink)
    equation_render_check: dict[str, int]  # render-back verdicts on image-backed equations
    equation_render_support: dict[str, int]  # LaTeX parseability under the bundled renderer
    equations: int
    equations_image_backed: int        # LaTeX the cross-check couldn't verify; crop is the source
    code_blocks: int
    illegible_blocks: int              # prose still symbol-font garbage after repair
    ocr_pages: int                     # pages with no text layer (scanned)
    vlm_pages: int                     # scanned pages transcribed whole by the vision model
    accounted_for: bool                # every engine-detected block has a disposition
    complete: bool                     # no detected block is flagged or dropped
    needs_review: bool                 # one or more action-required items need inspection
    review_flags: int
    review_reasons: dict[str, int]
    encoding_legibility: float         # fraction without broken-font/dingbat corruption, 0..1
    # Text-sufficiency: can a reader work from the markdown alone, or is the crop the real record?
    # Orthogonal to accounting (a scanned figure can be accounted for but not text-sufficient).
    text_sufficient: int               # elements usable as text/data from the markdown alone
    pixel_authoritative: int           # elements whose image crop is the authoritative record
    pixel_authoritative_by: dict[str, int]  # breakdown: scanned figures, image-backed tables, ...
    confidence: str                    # "high" | "medium" | "low"
    confidence_reasons: list[str]
    # Token-level consistency (born-digital docs; informational, see enrich.py).
    # Word recall compares each prose block's emitted text against the glyph layer
    # reading of its own source region.
    glyph_recall_blocks: int = 0             # blocks compared against the text layer
    glyph_recall_words_total: int = 0        # words in those source regions
    glyph_recall_words_matched: int = 0      # of those, present in the emitted text
    glyph_low_recall_blocks: int = 0         # blocks below the 0.90 recall floor
    numeric_conservation: dict[str, Any] = field(default_factory=dict)
    # ^ whole-document source-vs-output numeric token accounting (available /
    #   reason / counts / missing examples); empty when not computed
    quality_scorecard: dict[str, Any] = field(default_factory=dict)
    review_counts: dict[str, int] = field(default_factory=dict)


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
