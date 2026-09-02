# Non-zero page origins: engines report visible-area coordinates, pdfium is
# absolute user space. normalize_page_origin reconciles them at the seam and
# CropRenderer translates back when mapping into the rendered raster.

from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from pdf2md.engines.base import EngineResult, normalize_page_origin
from pdf2md.render import CropRenderer
from pdf2md.schema import BBox, Block, BlockType, FigureRef, RawCell, RawTable, TableData

_BB = BBox(x0=50.0, y0=100.0, x1=150.0, y1=50.0)


def _pdf(tmp_path: Path, crop: tuple[float, float, float, float]) -> Path:
    doc = pdfium.PdfDocument.new()
    pg = doc.new_page(612, 792)
    pg.set_mediabox(0, 0, 612, 792)
    pg.set_cropbox(*crop)
    path = tmp_path / "page.pdf"
    doc.save(str(path))
    doc.close()
    return path


def _result() -> EngineResult:
    return EngineResult(
        blocks=[Block(id="#/t/0", type=BlockType.PARAGRAPH, text="x", page=1, bbox=_BB)],
        tables=[TableData(block_id="#/tables/0", page=1, bbox=_BB, gfm="| x |")],
        figures=[FigureRef(block_id="#/pictures/0", page=1, bbox=_BB, caption_bbox=_BB)],
        page_sizes={1: (589.0, 783.0)},
        raw_tables={"#/tables/0": RawTable(
            cells=[RawCell(text="x", bbox=_BB, row=0, col=0, row_span=1, col_span=1,
                           header=False)],
            num_rows=1, num_cols=1,
        )},
    )


def test_normalize_page_origin_shifts_every_bbox_carrier(tmp_path):
    # The measured ACS case: MediaBox/CropBox corner at (9, 9). Every bbox --
    # block, table, raw cell, figure, caption -- moves by exactly the origin.
    result = _result()
    normalize_page_origin(result, _pdf(tmp_path, (9, 9, 598, 792)))
    shifted = BBox(x0=59.0, y0=109.0, x1=159.0, y1=59.0)
    assert result.blocks[0].bbox == shifted
    assert result.tables[0].bbox == shifted
    assert result.figures[0].bbox == shifted
    assert result.figures[0].caption_bbox == shifted
    assert result.raw_tables["#/tables/0"].cells[0].bbox == shifted


def test_normalize_page_origin_leaves_a_zero_origin_page_alone(tmp_path):
    result = _result()
    normalize_page_origin(result, _pdf(tmp_path, (0, 0, 612, 792)))
    assert result.blocks[0].bbox == _BB
    assert result.tables[0].bbox == _BB


def test_crop_translates_user_space_into_the_rendered_raster(tmp_path):
    # The raster covers the visible box, so a user-space bbox must be shifted by
    # the box corner before the Y flip. A marker pixel at a known user-space
    # point must land inside a crop of the bbox around that point.
    #
    # Marker at visible-relative (150, 150 from bottom) on a 589x783 page whose
    # corner sits at (9, 9): user space (159, 159). In top-left pixel space at
    # 72 dpi that is (150, 783 - 150) = (150, 633).
    renderer = CropRenderer(_pdf(tmp_path, (9, 9, 598, 792)), dpi=72, padding_pts=0.0)
    try:
        img = Image.new("RGB", (589, 783), "white")
        img.putpixel((150, 633), (255, 0, 0))
        renderer._page_cache[1] = ((589.0, 783.0), (9.0, 9.0), img)

        out = tmp_path / "crop.png"
        renderer.crop(1, BBox(x0=154.0, y0=164.0, x1=164.0, y1=154.0), out)
        cropped = Image.open(out)
        assert cropped.size == (10, 10)
        assert (255, 0, 0) in (px for px in cropped.getdata())
    finally:
        renderer.close()
