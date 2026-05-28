from __future__ import annotations

import hashlib
import re
from pathlib import Path

from docsmcp.store.schema import Block, BlockType, VerifyStatus


_PAGE_BREAK = re.compile(r"\n*\{(\d+)\}-{20,}\n+")


def _block_id(text: str, page: int, idx: int) -> str:
    return hashlib.sha1(f"marker:{page}:{idx}:{text[:200]}".encode()).hexdigest()[:12]


def _split_pages(markdown: str) -> list[tuple[int, str]]:
    """Split Marker's paginated output. Returns list of (page_no, page_md).

    Marker uses '{N}' + '-'*48 as page break when paginate_output=True.
    Returns 1-indexed page numbers; if no breaks found, returns whole doc as page 1.
    """
    matches = list(_PAGE_BREAK.finditer(markdown))
    if not matches:
        return [(1, markdown)]

    pages: list[tuple[int, str]] = []
    if matches[0].start() > 0:
        leading = markdown[: matches[0].start()].strip()
        if leading:
            pages.append((1, leading))
    for i, m in enumerate(matches):
        page_id = int(m.group(1)) + 1
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        chunk = markdown[start:end].strip()
        if chunk:
            pages.append((page_id, chunk))
    return pages


def transcribe(path: Path, *, profile: str = "balanced") -> tuple[str, list[Block], int, str]:
    """Run Marker on `path`. Returns (markdown, blocks, page_count, version)."""
    from importlib.metadata import version as _pkg_version

    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict
    from marker.output import text_from_rendered

    try:
        marker_version = _pkg_version("marker-pdf")
    except Exception:
        marker_version = "unknown"

    artifact_dict = create_model_dict()
    converter = PdfConverter(
        artifact_dict=artifact_dict,
        config={"paginate_output": True},
    )
    rendered = converter(str(path))
    markdown, _meta, _images = text_from_rendered(rendered)

    pages = _split_pages(markdown)
    blocks: list[Block] = []
    idx = 0
    for page_idx, page_md in pages:
        for para in (p.strip() for p in page_md.split("\n\n") if p.strip()):
            btype = BlockType.PARAGRAPH
            if para.startswith("#"):
                btype = BlockType.HEADING
            elif para.startswith("|") and "\n|" in para:
                btype = BlockType.TABLE
            elif para.startswith("$$") and para.endswith("$$"):
                btype = BlockType.EQUATION
            elif para.startswith("![") and "](" in para:
                btype = BlockType.FIGURE
            blocks.append(
                Block(
                    id=_block_id(para, page_idx, idx),
                    type=btype,
                    text=para,
                    page=page_idx,
                    bbox=None,
                    confidence=None,
                    engine="marker",
                    verify=VerifyStatus.UNVERIFIED,
                    extra={},
                )
            )
            idx += 1

    return markdown, blocks, len(pages), marker_version
