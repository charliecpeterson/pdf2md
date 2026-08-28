"""MinerU CLI adapter for high-accuracy scanned and structurally difficult PDFs.

MinerU runs in its own environment. This module translates its native middle JSON
into pdf2md types, while pdf2md re-renders every source crop and owns all output.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
from collections import deque
from contextlib import nullcontext
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterator

from pdf2md import __version__
from pdf2md.engines.base import EngineResult
from pdf2md.logging import Progress, get_logger
from pdf2md.scan_deskew import PreparedScan, deskew_scanned_pdf, restore_source_geometry
from pdf2md.schema import BBox, Block, BlockType, FigureLabels, FigureRef, TableData
from pdf2md.tables import GridCell, build_gfm

log = get_logger("engines.mineru")
_TASK_TIMEOUT_SECONDS = 6 * 60 * 60
_HEARTBEAT_SECONDS = 60.0
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_WINDOW_PROGRESS = re.compile(
    r"(?P<label>hybrid processing window)\s+(?P<completed>\d+)/(?P<total>\d+)",
    re.IGNORECASE,
)
_COUNTER_PROGRESS = re.compile(
    r"(?P<label>[A-Za-z][A-Za-z0-9 _/-]{0,40}?(?:Predict|Detection|Recognition))"
    r"\s*:.*?\b(?P<completed>\d+)/(?P<total>\d+)\b",
    re.IGNORECASE,
)


def _progress_counter(message: str) -> tuple[str, int, int, str] | None:
    """Read counters MinerU already prints without assigning them false page semantics."""
    clean = _ANSI_ESCAPE.sub("", message)
    match = _WINDOW_PROGRESS.search(clean)
    if match:
        return (
            "MinerU processing windows",
            int(match.group("completed")),
            int(match.group("total")),
            "windows",
        )
    match = _COUNTER_PROGRESS.search(clean)
    if not match:
        return None
    label = " ".join(match.group("label").split())
    return (
        f"MinerU {label}",
        int(match.group("completed")),
        int(match.group("total")),
        "items",
    )


def _capture_output(
    process: subprocess.Popen[str],
    progress: Progress,
    *,
    heartbeat_seconds: float = _HEARTBEAT_SECONDS,
) -> deque[str]:
    """Keep the CLI responsive while MinerU runs stages that print no counters."""
    tail: deque[str] = deque(maxlen=50)
    if process.stdout is None:
        return tail

    pending: queue.Queue[str | None] = queue.Queue()

    def read_stdout() -> None:
        try:
            for line in process.stdout:
                pending.put(line)
        finally:
            pending.put(None)

    threading.Thread(target=read_stdout, daemon=True).start()
    while True:
        try:
            line = pending.get(timeout=heartbeat_seconds)
        except queue.Empty:
            progress.stage("MinerU still working (no new engine output)")
            continue
        if line is None:
            return tail
        for message in filter(None, re.split(r"[\r\n]+", line)):
            message = message.strip()
            if not message:
                continue
            tail.append(message)
            log.debug("mineru: %s", message)
            counter = _progress_counter(message)
            if counter is not None:
                label, completed, total, unit = counter
                progress.count(label, completed, total, unit=unit)


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[tuple[str, int, int, bool]]] = []
        self._row: list[tuple[str, int, int, bool]] | None = None
        self._text: list[str] | None = None
        self._row_span = 1
        self._col_span = 1
        self._header = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            values = dict(attrs)
            self._text = []
            self._row_span = int(values.get("rowspan") or 1)
            self._col_span = int(values.get("colspan") or 1)
            self._header = tag == "th"
        elif tag == "eq" and self._text is not None:
            self._text.append("$")

    def handle_data(self, data: str) -> None:
        if self._text is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "eq" and self._text is not None:
            self._text.append("$")
        elif tag in {"td", "th"} and self._text is not None and self._row is not None:
            text = " ".join("".join(self._text).split()).replace("|", r"\|")
            self._row.append((text, self._row_span, self._col_span, self._header))
            self._text = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def _table_markup(html: str) -> tuple[str, bool]:
    parser = _TableParser()
    parser.feed(html)
    occupied: set[tuple[int, int]] = set()
    cells: list[GridCell] = []
    max_col = 0
    for row, source_cells in enumerate(parser.rows):
        col = 0
        for text, row_span, col_span, header in source_cells:
            while (row, col) in occupied:
                col += 1
            cells.append(GridCell(text, row, col, row_span, col_span, header))
            for covered_row in range(row, row + row_span):
                for covered_col in range(col, col + col_span):
                    if (covered_row, covered_col) != (row, col):
                        occupied.add((covered_row, covered_col))
            col += col_span
        max_col = max(max_col, col)
    spanning = any(cell.row_span > 1 or cell.col_span > 1 for cell in cells)
    return build_gfm(cells, len(parser.rows), max_col), spanning


def _spans(value: Any) -> Iterator[dict[str, Any]]:
    if not isinstance(value, dict):
        return
    for line in value.get("lines") or []:
        yield from line.get("spans") or []
    for block in value.get("blocks") or []:
        yield from _spans(block)


def _text(value: dict[str, Any]) -> str:
    parts = []
    for span in _spans(value):
        content = str(span.get("content") or "")
        if span.get("type") == "inline_equation" and content:
            content = f"${content}$"
        parts.append(content)
    return " ".join(part.strip() for part in parts if part.strip())


def _nested_blocks(value: dict[str, Any], block_type: str) -> list[dict[str, Any]]:
    return [block for block in value.get("blocks") or [] if block.get("type") == block_type]


def _bbox(value: Any, page_height: float) -> BBox | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    left, top, right, bottom = map(float, value)
    return BBox(left, page_height - top, right, page_height - bottom)


def _translate_middle(document: dict[str, Any], mineru_version: str = "unknown") -> EngineResult:
    blocks: list[Block] = []
    tables: list[TableData] = []
    figures: list[FigureRef] = []
    page_sizes: dict[int, tuple[float, float]] = {}
    serial = 0

    def block_id(page: int, kind: str) -> str:
        nonlocal serial
        serial += 1
        return f"#/mineru/{page}/{kind}/{serial}"

    for page_info in document.get("pdf_info") or []:
        page = int(page_info.get("page_idx", 0)) + 1
        width, height = map(float, page_info.get("page_size") or (0, 0))
        page_sizes[page] = (width, height)
        for item in page_info.get("preproc_blocks") or []:
            kind = item.get("type")
            bbox = _bbox(item.get("bbox"), height)
            if kind == "list":
                for entry in item.get("blocks") or []:
                    blocks.append(Block(
                        block_id(page, "list"), BlockType.LIST, _text(entry), page,
                        _bbox(entry.get("bbox"), height), engine="mineru",
                    ))
                continue
            if kind in {"text", "title"}:
                block_type = BlockType.HEADING if kind == "title" else BlockType.PARAGRAPH
                blocks.append(Block(
                    block_id(page, kind), block_type, _text(item), page, bbox,
                    engine="mineru",
                ))
                continue
            if kind == "interline_equation":
                blocks.append(Block(
                    block_id(page, "equation"), BlockType.EQUATION, _text(item), page,
                    bbox, engine="mineru",
                ))
                continue
            if kind == "table":
                captions = _nested_blocks(item, "table_caption")
                caption = _text(captions[0]) if captions else ""
                if caption:
                    blocks.append(Block(
                        block_id(page, "caption"), BlockType.CAPTION, caption, page,
                        _bbox(captions[0].get("bbox"), height), engine="mineru",
                    ))
                table_span = next(
                    (span for span in _spans(item) if span.get("type") == "table"), None
                )
                html = str((table_span or {}).get("html") or "")
                gfm, spanning = _table_markup(html) if html else ("", False)
                table_id = block_id(page, "table")
                blocks.append(Block(table_id, BlockType.TABLE, "", page, bbox, engine="mineru"))
                tables.append(TableData(
                    table_id, page, bbox, gfm=gfm,
                    html=html if spanning else None, has_spanning_cells=spanning,
                ))
                continue
            if kind in {"image", "chart"}:
                caption_blocks = _nested_blocks(item, "image_caption") + _nested_blocks(
                    item, "chart_caption"
                )
                caption = _text(caption_blocks[0]) if caption_blocks else None
                body_blocks = _nested_blocks(item, "image_body") + _nested_blocks(
                    item, "chart_body"
                )
                labels_text = "\n".join(filter(None, (_text(body) for body in body_blocks)))
                figure_id = block_id(page, "figure")
                blocks.append(Block(
                    figure_id, BlockType.FIGURE, "", page, bbox, engine="mineru"
                ))
                figures.append(FigureRef(
                    figure_id,
                    page,
                    bbox,
                    caption=caption,
                    caption_bbox=(
                        _bbox(caption_blocks[0].get("bbox"), height) if caption_blocks else None
                    ),
                    labels=(
                        FigureLabels(
                            labels_text,
                            0.7,
                            "printed text read by MinerU; verify against the source crop",
                        )
                        if labels_text else None
                    ),
                ))

    return EngineResult(
        blocks,
        tables,
        figures,
        page_sizes,
        engine_versions={"mineru": mineru_version, "pdf2md": __version__},
    )


class MinerUEngine:
    name = "mineru"

    def __init__(self, executable: str = "mineru", *, deskew_scans: bool = True) -> None:
        path = Path(executable).expanduser()
        resolved = str(path.resolve()) if path.parent != Path(".") else shutil.which(executable)
        if not resolved or not Path(resolved).is_file():
            raise RuntimeError(
                f"MinerU executable not found: {executable}. Install it in a separate "
                "environment and set mineru_executable in the config."
            )
        self.executable = resolved
        self.deskew_scans = deskew_scans
        probe = subprocess.run(
            [self.executable, "--version"], capture_output=True, text=True, check=False
        )
        self.version = (probe.stdout or probe.stderr).strip() or "unknown"

    def cache_identity(self) -> str:
        return f"{self.executable}:{self.version}:deskew={self.deskew_scans}"

    def convert(self, pdf_path: Path) -> EngineResult:
        log.info("mineru converting %s", pdf_path)
        progress = Progress(log)
        prepared_input = (
            deskew_scanned_pdf(pdf_path)
            if self.deskew_scans
            else nullcontext(PreparedScan(pdf_path, {}))
        )
        with prepared_input as prepared, \
                tempfile.TemporaryDirectory(prefix="pdf2md-mineru-") as temp:
            if prepared.angles:
                details = ", ".join(
                    f"page {page}: {angle:+g}°"
                    for page, angle in prepared.angles.items()
                )
                log.info("deskewed scanned pages before MinerU: %s", details)
            output = Path(temp)
            command = [
                self.executable,
                "-p", str(prepared.path),
                "-o", str(output),
                "-b", "hybrid-engine",
                "--effort", "high",
                "--image-analysis", "true",
            ]
            environment = os.environ.copy()
            # MinerU's one-hour default expires on large high-effort documents.
            environment.setdefault(
                "MINERU_TASK_RESULT_TIMEOUT_SECONDS", str(_TASK_TIMEOUT_SECONDS)
            )
            process = subprocess.Popen(
                command,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                bufsize=1,
            )
            tail = _capture_output(process, progress)
            returncode = process.wait()
            if returncode != 0:
                detail = "\n".join(tail)
                raise RuntimeError(f"MinerU failed with exit code {returncode}: {detail}")
            middle = list(output.rglob("*_middle.json"))
            if len(middle) != 1:
                raise RuntimeError(f"MinerU produced {len(middle)} middle JSON files, expected 1")
            result = _translate_middle(json.loads(middle[0].read_text()), self.version)
            restore_source_geometry(result, prepared.angles)
            return result
