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
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from pdf2md import __version__
from pdf2md.engines.base import EngineResult
from pdf2md.engines.streaming import ANSI_ESCAPE, capture_output
from pdf2md.logging import Progress, get_logger
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
        self._math: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.parts.append("\n")
        elif tag == "math":
            # Marker carries LaTeX inside <math>; dropping the tag without adding
            # delimiters leaves mathematics that no reader can tell from prose, and
            # the script overlay then rewrites `^n` into HTML inside the LaTeX.
            delim = "$$" if dict(attrs).get("display") == "block" else "$"
            self._math.append(delim)
            self.parts.append(delim)

    def handle_endtag(self, tag: str) -> None:
        if tag == "math" and self._math:
            self.parts.append(self._math.pop())
        elif tag in self._BREAKS:
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
    empty = 0

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

            text = _text(html)
            if _TYPES.get(kind) is BlockType.EQUATION:
                # A display equation is the whole block, and emit adds the `$$`
                # fences itself (emit.py). Leaving the adapter's own delimiters on
                # double-wraps all 1,895 of them and leaves the LaTeX unbalanced.
                # Every delimiter goes, not just an enclosing pair: 66 blocks hold
                # several <math display="block"> spans, and stripping only the
                # outer pair strands the inner ones (`\tag{2.17}$$$$t = ...`).
                text = text.replace("$$", "\n").strip()
            if not text:
                # Marker marks a region and sometimes transcribes nothing into it (a
                # detected PageHeader it did not read, most often). That is a layout
                # region, not content, and admitting it as an empty block makes the
                # coverage audit report content dropped where the engine never
                # offered any -- 163 such blocks across seven documents, against
                # none from Docling. Tables and figures are handled above and keep
                # their empty text, because their content is the crop.
                empty += 1
                continue
            blocks.append(Block(
                block_id, _TYPES.get(kind, BlockType.OTHER), text, index, bbox,
                engine="marker",
            ))

    if empty:
        log.info("marker: skipped %d region(s) it transcribed no text into", empty)
    return EngineResult(
        blocks,
        tables,
        figures,
        page_sizes,
        engine_versions={"marker": marker_version, "pdf2md": __version__},
    )


def _flatten(children: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace every group by its members.

    A group's own `html` is not its content: Marker fills it with
    `<content-ref src='/page/0/Table/11'></content-ref>` stubs and puts the real
    markup on the child. Treating a TableGroup as the unit therefore handed the
    table converter a reference and produced an empty grid -- 240 table tests that
    Marker itself passes.
    """
    out: list[dict[str, Any]] = []
    for item in children:
        nested = item.get("children") or []
        if nested:
            out.extend(_flatten(nested))
        else:
            out.append(item)
    return out


_HEARTBEAT_SECONDS = 60.0
_UNREACHABLE = re.compile(r"(SURYA_INFERENCE_URL=\S+) is not reachable")


def _server_message(url: str, reason: str) -> str:
    return (
        f"Marker's inference server at {url} is not responding ({reason}). Start it "
        "(scripts/start_surya_vllm.sh) and check it answers on /health, or unset "
        "SURYA_INFERENCE_URL to let Marker try to start its own. "
        "`pdf2md doctor --engine marker` reports which of these is missing."
    )


def _failure(returncode: int, detail: str) -> str:
    """Say what actually went wrong when Marker cannot reach an inference server.

    There are two different causes and they need opposite advice. With SURYA_INFERENCE_URL set the server
    is simply not answering (it died or was never started); without it Marker's Surya spawns a Docker
    container with the `nvidia` runtime, which many hosts do not register even with the container toolkit
    installed. Both arrive as a SpawnError at the bottom of a traceback, and lumping them together sent a
    user with a healthy docker setup to register a runtime they did not need.
    """
    unreachable = _UNREACHABLE.search(detail)
    if unreachable:
        url = unreachable.group(1).split("=", 1)[1]
        return f"{_server_message(url, 'not reachable at /health')}\nMarker exit code {returncode}: {detail[-600:]}"
    if "unknown or invalid runtime name: nvidia" in detail or "SpawnError" in detail:
        return (
            "Marker could not start its inference server: docker has no `nvidia` "
            "runtime registered. Start a server yourself and point Marker at it "
            "(scripts/start_surya_vllm.sh, then export "
            "SURYA_INFERENCE_URL=http://127.0.0.1:8000/v1), or register the runtime "
            "with `sudo nvidia-ctk runtime configure --runtime=docker`. "
            "`pdf2md doctor --engine marker` reports which of these is missing.\n"
            f"Marker exit code {returncode}: {detail[-600:]}"
        )
    return f"Marker failed with exit code {returncode}: {detail}"


# tqdm draws one bar per stage ("Recognizing Layout: 40%|████ | 170/423 [...]"); report each as its own counter.
_BAR = re.compile(
    r"(?P<label>[A-Za-z][A-Za-z0-9 _/()-]{2,60}?)\s*:\s*\d+%\|[^|]*\|\s*(?P<completed>\d+)/(?P<total>\d+)"
)


def _progress_counter(message: str) -> tuple[str, int, int, str] | None:
    match = _BAR.search(ANSI_ESCAPE.sub("", message))
    if not match:
        return None
    return (
        f"Marker {' '.join(match.group('label').split())}",
        int(match.group("completed")),
        int(match.group("total")),
        "items",
    )


def _require_server() -> None:
    """Fail in seconds, not after a model load, when SURYA_INFERENCE_URL points at a server that is not answering."""
    url = os.environ.get("SURYA_INFERENCE_URL")
    if not url:
        return
    root = url.rstrip("/").removesuffix("/v1")
    try:
        urllib.request.urlopen(f"{root}/health", timeout=10).close()
    except (OSError, ValueError) as error:
        raise RuntimeError(_server_message(url, str(error))) from error


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
        _require_server()
        progress = Progress(log)
        with tempfile.TemporaryDirectory(prefix="pdf2md-marker-") as temp:
            output = Path(temp)
            process = subprocess.Popen(
                [self.executable, str(pdf_path), "--output_format", "json", "--output_dir", str(output)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                bufsize=1,
            )
            tail = capture_output(
                process,
                progress,
                _progress_counter,
                still_working="Marker still working (no new engine output)",
                heartbeat_seconds=_HEARTBEAT_SECONDS,
                debug=lambda message: log.debug("marker: %s", message),
            )
            returncode = process.wait()
            if returncode != 0:
                raise RuntimeError(_failure(returncode, "\n".join(tail)[-2000:]))
            produced = [p for p in output.rglob("*.json") if not p.name.endswith("_meta.json")]
            if len(produced) != 1:
                raise RuntimeError(
                    f"Marker produced {len(produced)} JSON files, expected 1"
                )
            document = json.loads(produced[0].read_text(encoding="utf-8"))
        return _translate(document, self.version)
