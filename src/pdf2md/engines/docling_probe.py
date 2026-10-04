"""Find the page that kills docling-parse, without losing the conversion to it.

docling-parse is a C++ extension, and on some pages it traps (SIGTRAP, exit 133)
instead of raising. A trap takes the whole interpreter with it, so a batch's
per-document error handling never runs and the document yields nothing. Measured
on a 850-page nuclear chemistry textbook: page 633 carries a 5134x2518 JBIG2
stencil figure, and decoding its bitmap content crashes the parser. The same page
parses when bitmap content is skipped, and with or without pixel bytes, and
parsed on its own it makes no difference whether sanitisation or native-memory
release is configured.

The probe runs the parser alone, one page at a time, in a throwaway process with
the content settings Docling uses. Parsing is about a tenth of a second a page,
so it costs a few percent of a conversion, and it turns a dead process into a
page number the caller can act on. It reports only a crash: any ordinary error is
left for the real conversion to raise with its own message.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pdf2md.logging import get_logger

log = get_logger("docling_probe")


def _parse_every_page(pdf_path: str) -> int:
    """Child entry point: print each page number once it has parsed."""
    from docling_parse.pdf_parser import (
        ContentConfig,
        ContentLevel,
        DecodeConfig,
        DoclingPdfParser,
    )

    # The settings Docling's own backend passes, so the probe sees what it will see.
    content = ContentConfig(
        char_cells_content_level=ContentLevel.COMPUTE,
        word_cells_content_level=ContentLevel.COMPUTE_AND_MATERIALIZE,
        line_cells_content_level=ContentLevel.COMPUTE_AND_MATERIALIZE,
        shapes_content_level=ContentLevel.SKIP,
        bitmaps_content_level=ContentLevel.COMPUTE_AND_MATERIALIZE,
        include_bitmap_bytes=False,
    )
    doc = DoclingPdfParser().load(pdf_path, decode_config=DecodeConfig(enforce_same_font=True))
    for page_no in range(1, doc.number_of_pages() + 1):
        doc.get_page(page_no, content_config=content)
        print(page_no, flush=True)
    return 0


def crashing_page(pdf_path: Path) -> int | None:
    """The 1-based page docling-parse dies on, or None if it parses every page.

    A negative return code means the child was killed by a signal, which is the
    only outcome reported. A parse error exits normally with a traceback and is
    returned as None, leaving the conversion to meet and report it."""
    proc = subprocess.run(
        [sys.executable, "-m", "pdf2md.engines.docling_probe", str(pdf_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode >= 0:
        if proc.returncode:
            log.debug("docling-parse probe failed without crashing: %s", proc.stderr.strip()[-300:])
        return None
    parsed = [int(line) for line in proc.stdout.split() if line.isdigit()]
    return (parsed[-1] if parsed else 0) + 1


if __name__ == "__main__":
    sys.exit(_parse_every_page(sys.argv[1]))
