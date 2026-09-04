"""Marker CLI adapter.

Marker runs in its own environment, like MinerU, and this module translates its
native JSON block tree into pdf2md types. Measured over olmOCR-bench it reads
markdown better than Docling on every subset (74.7% against 55.4%), and it is the
only engine here that emits inline mathematics as LaTeX, which is the single
largest gap that benchmark exposed.

Two limits worth knowing before reading the code. Marker's JSON renderer recurses
into a block only when its class does not derive directly from `Block`, and
`TableCell` does, so cells are flattened into the table's HTML and never appear as
children: tables arrive as markup, not positioned cells, and this adapter supplies
no `raw_tables`. That matches the MinerU adapter and costs the same thing -- the
per-cell glyph verification in `enrich` and `table_audit` has nothing to attach
to. And Marker's Surya drives a vLLM backend that expects a Docker container with
the `nvidia` runtime registered; where it is not, point `SURYA_INFERENCE_URL` at a
server started by hand (`scripts/start_surya_vllm.sh`).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from pdf2md import __version__
from pdf2md.engines.base import EngineResult
from pdf2md.logging import get_logger
from pdf2md.schema import BBox, Block, BlockType, FigureLabels, FigureRef, TableData
from pdf2md.tables import html_to_gfm

log = get_logger("engines.marker")

# Marker's own names, from marker/schema/__init__.py. Anything absent here lands
# on OTHER rather than being dropped, so a new Marker block type cannot silently
# remove content from the output.
_TYPES = {
    "Text": BlockType.PARAGRAPH,
    "TextInlineMath": BlockType.PARAGRAPH,
    "SectionHeader": BlockType.HEADING,
    "ListItem": BlockType.LIST,
    "ListGroup": BlockType.LIST,
    "Table": BlockType.TABLE,
    "TableGroup": BlockType.TABLE,
    "Form": BlockType.TABLE,
    "Figure": BlockType.FIGURE,
    "Picture": BlockType.FIGURE,
    "Diagram": BlockType.FIGURE,
    "FigureGroup": BlockType.FIGURE,
    "PictureGroup": BlockType.FIGURE,
    "Equation": BlockType.EQUATION,
    "Code": BlockType.CODE,
    "Caption": BlockType.CAPTION,
    "Footnote": BlockType.FOOTNOTE,
    "PageHeader": BlockType.PAGE_HEADER,
    "PageFooter": BlockType.PAGE_FOOTER,
    "TableOfContents": BlockType.OTHER,
    "Reference": BlockType.OTHER,
    "Bibliography": BlockType.OTHER,
    "Handwriting": BlockType.PARAGRAPH,
    "ComplexRegion": BlockType.OTHER,
    "ChemicalBlock": BlockType.OTHER,
}
_FIGURE_TYPES = {"Figure", "Picture", "Diagram", "FigureGroup", "PictureGroup"}
_TABLE_TYPES = {"Table", "TableGroup", "Form"}


class _TextParser(HTMLParser):
    """Marker carries block content as HTML; this is the visible text of it.

    `<br>` and block-level closes become newlines because a line break Marker drew
    is structure the emitter needs, and everything else collapses to spaces.
    """

    _BREAKS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BREAKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _text(html: str) -> str:
    parser = _TextParser()
    parser.feed(html or "")
    lines = [" ".join(line.split()) for line in "".join(parser.parts).split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _bbox(value: Any, page_height: float) -> BBox | None:
    """Marker's boxes are top-left origin; pdf2md's are bottom-left."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    left, top, right, bottom = (float(v) for v in value)
    return BBox(left, page_height - top, right, page_height - bottom)


def _page_size(page: dict[str, Any]) -> tuple[float, float]:
    box = page.get("bbox")
    if isinstance(box, (list, tuple)) and len(box) == 4:
        left, top, right, bottom = (float(v) for v in box)
        return right - left, bottom - top
    return 0.0, 0.0


def _translate(document: dict[str, Any], marker_version: str = "unknown") -> EngineResult:
    blocks: list[Block] = []
    tables: list[TableData] = []
    figures: list[FigureRef] = []
    page_sizes: dict[int, tuple[float, float]] = {}

    for index, page in enumerate(document.get("children") or [], start=1):
        width, height = _page_size(page)
        page_sizes[index] = (width, height)
        for item in _flatten(page.get("children") or []):
            kind = str(item.get("block_type") or "")
            bbox = _bbox(item.get("bbox"), height)
            block_id = str(item.get("id") or f"#/marker/{index}/{len(blocks)}")
            html = str(item.get("html") or "")

            if kind in _TABLE_TYPES:
                gfm, spanning = html_to_gfm(html) if html else ("", False)
                blocks.append(Block(block_id, BlockType.TABLE, "", index, bbox,
                                    engine="marker"))
                tables.append(TableData(
                    block_id, index, bbox, gfm=gfm,
                    html=html if spanning else None, has_spanning_cells=spanning,
                ))
                continue

            if kind in _FIGURE_TYPES:
                labels = _text(html)
                blocks.append(Block(block_id, BlockType.FIGURE, "", index, bbox,
                                    engine="marker"))
                figures.append(FigureRef(
                    block_id, index, bbox,
                    labels=(
                        FigureLabels(
                            labels, 0.7,
                            "printed text read by Marker; verify against the source crop",
                        ) if labels else None
                    ),
                ))
                continue

            blocks.append(Block(
                block_id, _TYPES.get(kind, BlockType.OTHER), _text(html), index, bbox,
                engine="marker",
            ))

    return EngineResult(
        blocks,
        tables,
        figures,
        page_sizes,
        engine_versions={"marker": marker_version, "pdf2md": __version__},
    )


def _flatten(children: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group blocks carry their members as children and their own assembled HTML.

    Taking both would emit the content twice, so a group is replaced by its members
    -- except a table or figure group, which is the unit pdf2md crops and renders.
    """
    out: list[dict[str, Any]] = []
    for item in children:
        kind = str(item.get("block_type") or "")
        nested = item.get("children") or []
        if nested and kind not in _TABLE_TYPES and kind not in _FIGURE_TYPES:
            out.extend(_flatten(nested))
        else:
            out.append(item)
    return out


class MarkerEngine:
    name = "marker"

    def __init__(self, executable: str = "marker_single") -> None:
        path = Path(executable).expanduser()
        resolved = str(path.resolve()) if path.parent != Path(".") else shutil.which(executable)
        if not resolved or not Path(resolved).is_file():
            raise RuntimeError(
                f"Marker executable not found: {executable}. Install marker-pdf in a "
                "separate environment and set marker_executable in the config."
            )
        self.executable = resolved
        probe = subprocess.run(
            [self.executable, "--version"], capture_output=True, text=True, check=False
        )
        self.version = (probe.stdout or probe.stderr).strip().splitlines()[:1]
        self.version = self.version[0] if self.version else "unknown"

    def cache_identity(self) -> str:
        return f"{self.executable}:{self.version}"

    def convert(self, pdf_path: Path) -> EngineResult:
        log.info("marker converting %s", pdf_path)
        with tempfile.TemporaryDirectory(prefix="pdf2md-marker-") as temp:
            output = Path(temp)
            result = subprocess.run(
                [self.executable, str(pdf_path), "--output_format", "json",
                 "--output_dir", str(output)],
                capture_output=True, text=True, errors="replace",
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "")[-2000:]
                raise RuntimeError(f"Marker failed with exit code {result.returncode}: {detail}")
            produced = [p for p in output.rglob("*.json") if not p.name.endswith("_meta.json")]
            if len(produced) != 1:
                raise RuntimeError(
                    f"Marker produced {len(produced)} JSON files, expected 1"
                )
            document = json.loads(produced[0].read_text(encoding="utf-8"))
        return _translate(document, self.version)
