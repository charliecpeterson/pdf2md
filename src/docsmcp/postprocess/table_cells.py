"""Parse markdown tables into (row, col, text) cells.

Used for per-cell verification: feeds rows of cells into VLM checks against the
rendered table image.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_NUMERIC = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")


@dataclass
class Cell:
    row_idx: int  # 1-indexed across header + data rows
    col_idx: int  # 1-indexed
    text: str
    is_header: bool = False


@dataclass
class CellTarget:
    """A cell selected for verification, addressed by labels (more robust than indices)."""
    row_label: str   # Leftmost cell of the data row (e.g., '1s', 'CH(2Π)')
    col_label: str   # Column header text (joined if multi-row header)
    row_idx: int     # Original row index in the parsed table
    col_idx: int     # Original col index
    text: str        # The cell's extracted text


def parse_markdown_table(markdown: str) -> list[Cell]:
    """Parse a markdown pipe table into a flat list of Cells.

    Detects multi-row headers via content heuristic: a row is treated as a header
    if more than half its cells are non-numeric labels (covers scientific tables
    that have both a column-group header and a column-name header).
    """
    if not markdown:
        return []
    rows_text: list[str] = []
    for line in markdown.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            rows_text.append(s)
    if not rows_text:
        return []

    sep_idx: int | None = None
    for i, row in enumerate(rows_text):
        if re.match(r"^\|[\s\-:|]+\|$", row):
            sep_idx = i
            break

    # First pass: parse all rows
    raw_rows: list[list[str]] = []
    row_indices: list[int] = []
    for i, row in enumerate(rows_text):
        if i == sep_idx:
            continue
        parts = [c.strip() for c in row.strip("|").split("|")]
        raw_rows.append(parts)
        row_indices.append(i + 1)

    # Detect data rows: most non-placeholder cells are numeric. Placeholders
    # (`. . .`, `—`) are excluded from the ratio so a sparse row with a couple
    # of data values still classifies as data.
    def _row_is_data(row: list[str]) -> bool:
        non_placeholder = [c for c in row if c and not is_placeholder_cell(c)]
        if not non_placeholder:
            return False
        numeric = sum(1 for c in non_placeholder if is_numeric_cell(c))
        return numeric * 2 >= len(non_placeholder)

    cells: list[Cell] = []
    for parts, ridx in zip(raw_rows, row_indices):
        # Anything before the separator is header by default
        is_header_pos = (sep_idx is not None and ridx - 1 < sep_idx)
        # Anything after the separator that isn't a data row is also header
        # (covers multi-row headers below the separator)
        is_header_content = (not is_header_pos) and (not _row_is_data(parts))
        is_header = is_header_pos or is_header_content
        for j, val in enumerate(parts, start=1):
            cells.append(Cell(row_idx=ridx, col_idx=j, text=val, is_header=is_header))
    return cells


_LATEX_LABEL_SUBS = {
    "/Pi1": "π", "/Sigma1": "Σ", "/Delta1": "Δ", "/Gamma1": "Γ",
    "/Phi1": "φ", "/Psi1": "ψ", "/Omega1": "Ω", "/Lambda1": "Λ",
    "/Theta1": "θ", "/alpha1": "α", "/beta1": "β", "/gamma1": "γ",
    "/delta1": "δ", "/epsilon1": "ε", "/zeta1": "ζ", "/eta1": "η",
    "/lambda1": "λ", "/mu1": "μ", "/nu1": "ν", "/omega1": "ω",
    "/pi1": "π", "/rho1": "ρ", "/sigma1": "σ", "/tau1": "τ",
    "/phi1": "φ", "/chi1": "χ", "/psi1": "ψ", "/theta1": "θ",
}


def clean_label_for_vlm(text: str) -> str:
    """Replace Docling's LaTeX-ish label encoding (`/Pi1` → π etc.) with the
    Unicode chars the VLM will actually see in the rendered image."""
    if not text:
        return text
    out = text
    for k, v in _LATEX_LABEL_SUBS.items():
        out = out.replace(k, v)
    return out


_PLACEHOLDER_CELL = {". . .", "...", "—", "-", "n/a", "N/A", ""}


def is_placeholder_cell(text: str) -> bool:
    """True for cells that just denote 'no data' — `. . .`, `—`, blank, etc."""
    if not text:
        return True
    return text.strip().lower() in {p.lower() for p in _PLACEHOLDER_CELL}


def is_numeric_cell(text: str) -> bool:
    """Cell is predominantly numeric (a data value, not a label with a year in it).

    Rejects `/Delta1 f H ◦ 298` (>60% non-numeric chars) but accepts `2.398`,
    `79.9 (Ref. 88)`, `-2861.679`.
    """
    if not text:
        return False
    stripped = text.strip()
    if not stripped or is_placeholder_cell(stripped):
        return False
    if not any(c.isdigit() for c in stripped):
        return False
    if not _NUMERIC.search(stripped):
        return False
    # Require text to be *mostly* numeric / decimal / sign / scientific characters
    numeric_chars = sum(
        1 for c in stripped if c.isdigit() or c in ".-+eE×× ()"
    )
    return numeric_chars / len(stripped) >= 0.55


def select_cells_for_verification(
    cells: list[Cell], *, only_numeric: bool = True, limit: int = 10
) -> list[Cell]:
    """Pick representative cells to verify — non-header, optionally numeric, capped."""
    pool = [c for c in cells if not c.is_header and c.text]
    if only_numeric:
        pool = [c for c in pool if is_numeric_cell(c.text)]
    return pool[:limit]


def select_targets_for_verification(
    cells: list[Cell], *, only_numeric: bool = True, limit: int = 10
) -> list[CellTarget]:
    """Pick verification targets addressed by (row_label, col_label) rather than index.

    Combines column headers across multiple header rows (e.g., the column-group
    header + the column-name header) into a single col_label like "MR-ccCA / TAE".
    Skips the leftmost column when it appears to be the row label column itself.
    """
    rows: dict[int, list[Cell]] = {}
    for c in cells:
        rows.setdefault(c.row_idx, []).append(c)

    header_rows = sorted(r for r, row in rows.items() if any(c.is_header for c in row))
    data_rows = sorted(r for r in rows if r not in header_rows)

    # Column label = " / ".join(non-empty headers in that column, top to bottom)
    def _col_label(col_idx: int) -> str:
        parts: list[str] = []
        for hr in header_rows:
            for c in rows[hr]:
                if c.col_idx == col_idx and c.text and c.text not in parts:
                    parts.append(c.text)
        return " / ".join(parts) if parts else f"col{col_idx}"

    targets: list[CellTarget] = []
    for ridx in data_rows:
        row = sorted(rows[ridx], key=lambda c: c.col_idx)
        if not row:
            continue
        row_label = row[0].text or f"row{ridx}"
        for c in row[1:]:  # skip leftmost (row label)
            if not c.text:
                continue
            if only_numeric and not is_numeric_cell(c.text):
                continue
            targets.append(
                CellTarget(
                    row_label=clean_label_for_vlm(row_label),
                    col_label=clean_label_for_vlm(_col_label(c.col_idx)),
                    row_idx=c.row_idx,
                    col_idx=c.col_idx,
                    text=c.text,
                )
            )
            if len(targets) >= limit:
                return targets
    return targets
