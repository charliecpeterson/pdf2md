from __future__ import annotations

from pdf2md.coverage import build_report
from pdf2md.emit import _tidy_math, emit_document
from pdf2md.schema import FORMAT_VERSION, CoverageStatus
from pdf2md.structure import build_structure


def _emit(tmp_path, doc):
    structure = build_structure(doc.blocks, None, title="Doc", page_count=doc.page_count)
    meta = {"title": "Doc", "authors": ["A. Author"], "year": "2021", "doi": None}
    return emit_document(doc, structure, tmp_path, meta, {"docling": "2.93.0", "pdf2md": "0.1.0"})


def test_tidy_math_strips_spacing_blowups():
    # Docling pads trailing PDF whitespace with runaway \quad / control-spaces.
    trail = r"2 \pi _ { u } ^ { 2 } . \quad \ \ ( 9 ) \quad \ \ \ \ \ \ \ " + "\\"
    assert _tidy_math(trail) == r"2 \pi _ { u } ^ { 2 } .  \quad ( 9 )"

    # ...and pads lost alignment columns with repeated empty `& \quad` cells.
    cells = r"E = E ( X ) & \quad & \quad & \quad & \quad"
    assert _tidy_math(cells) == r"E = E ( X )"

    # Legitimate multi-column equations (single `& \quad`, real `\\`) are untouched.
    aligned = r"\Delta E & = E ( A ) & \quad \\ & - E ( B ) & \quad ( 5 )"
    assert _tidy_math(aligned) == aligned

    # A garbled equation with an unclosed brace (Docling misread `}` as `)`) gets
    # padded so KaTeX renders it instead of dumping the raw source.
    garbled = r"E ( \text {MR-AQC/CC) - E ( \text {x} )"
    fixed = _tidy_math(garbled)
    assert fixed.count("{") == fixed.count("}") == 2


def test_table_data_renders_even_when_block_mislabelled():
    from pdf2md.emit import _Ctx, _render_block
    from pdf2md.schema import Block, BlockType, CoverageStatus, TableData

    # Docling labels a TOC page 'other' but still parses cells; the data must render
    # rather than the block being dropped as empty.
    td = TableData(block_id="#/tables/2", page=21, bbox=None,
                   gfm="| Chapter | Page |\n|---|---|\n| 1 | 5 |")
    ctx = _Ctx(depth_of={}, tables={"#/tables/2": td}, figures={})
    blk = Block(id="#/tables/2", type=BlockType.OTHER, text="", page=21)
    text, status, _ = _render_block(blk, ctx, [])
    assert "| Chapter | Page |" in text and status == CoverageStatus.EMITTED


def test_failed_table_falls_back_to_image():
    from pdf2md.emit import _Ctx, _render_block
    from pdf2md.schema import Block, BlockType, CoverageStatus

    ctx = _Ctx(depth_of={}, tables={}, figures={})
    # A table Docling couldn't parse (type 'other', empty text) but with a crop:
    # emit the image instead of dropping the region.
    blk = Block(id="#/tables/2", type=BlockType.OTHER, text="", page=21,
                extra={"crop_path": "assets/tables_2_p21.png"})
    text, status, flag = _render_block(blk, ctx, [])
    assert "![table](assets/tables_2_p21.png)" in text
    assert status == CoverageStatus.CROPPED and flag is not None

    # On a scanned page the marker says the OCR text is unreliable.
    ocr = Block(id="#/tables/3", type=BlockType.OTHER, text="", page=5,
                extra={"crop_path": "assets/tables_3_p5.png", "ocr": True})
    text, status, _ = _render_block(ocr, ctx, [])
    assert "scanned page" in text and "![table](assets/tables_3_p5.png)" in text


def test_balance_delims():
    from pdf2md.emit import _balance_delims

    # one \left, two \right won't compile in KaTeX -> drop the sizing commands.
    bad = r"\left\langle a \right| b \right\rangle"
    assert _balance_delims(bad) == r"\langle a | b \rangle"
    # a balanced pair is left untouched.
    ok = r"\left( a + b \right)"
    assert _balance_delims(ok) == ok


def test_low_confidence_equation_uses_image_and_hint():
    from pdf2md.emit import _Ctx, _render_block
    from pdf2md.schema import Block, BlockType, CoverageStatus

    ctx = _Ctx(depth_of={}, tables={}, figures={})

    # Suspect extraction with an ordered, clean text layer: image is authoritative,
    # the text-layer reading rides along as the hint.
    eq = Block(id="#/texts/9", type=BlockType.EQUATION, text="E ( garbled )", page=3,
               confidence=0.6, extra={"crop_path": "assets/eq_p3.png",
                                      "text_layer": "E(MR-AQCC/cc-pVTZ) (4)", "ordered": True})
    text, status, flag = _render_block(eq, ctx, [])
    assert "![equation](assets/eq_p3.png)" in text
    assert "E(MR-AQCC/cc-pVTZ) (4)" in text          # the reading hint, not $$ LaTeX
    assert status == CoverageStatus.CROPPED and flag is not None

    # Scrambled text layer (ordered=False): fall back to the vision LaTeX as the hint.
    eq2 = Block(id="#/texts/10", type=BlockType.EQUATION, text="E _ { n } = E _ { CBS }", page=3,
                confidence=0.0, extra={"crop_path": "assets/eq2_p3.png",
                                       "text_layer": "E E n CBS scrambled", "ordered": False})
    text2, _, _ = _render_block(eq2, ctx, [])
    assert "![equation](assets/eq2_p3.png)" in text2 and "$$" in text2  # LaTeX hint, not soup
    assert "scrambled" not in text2


def test_emit_structural_facts(tmp_path, sample_document):
    md_files, flags = _emit(tmp_path, sample_document)
    assert [p.name for p in md_files] == ["document.md"]
    text = md_files[0].read_text()

    assert f"format_version: '{FORMAT_VERSION}'" in text
    assert "engine_versions:" in text and "\nengine:" not in text
    assert "# 1 Introduction" in text          # heading depth 1
    assert "## 1.1 Background" in text          # nested heading depth 2
    assert "<!-- page 1 -->" in text and "<!-- page 2 -->" in text
    assert "![Figure 1](assets/pictures_0_p2.png)" in text
    assert "| a | b |" in text                  # table, caption stripped
    assert "$$" in text and "E = mc^2" in text  # equation as LaTeX
    assert "[^fn1]: a footnote" in text         # footnote collected
    assert "[pdf2md:" in text                   # the empty block emits a marker


def test_emit_is_lossless(tmp_path, sample_document):
    _, flags = _emit(tmp_path, sample_document)
    report = build_report(sample_document.doc_id, sample_document.blocks, flags)
    assert report.lossless
    assert report.cropped == 1          # the figure
    assert report.dropped == 1          # the empty paragraph
    # every block was accounted for
    assert all(b.coverage_status != CoverageStatus.PENDING for b in sample_document.blocks)


def test_illegible_footnote_flagged_not_emitted():
    # A broken-font footnote is symbol-font garbage like any prose; it must be flagged,
    # not appended to the footnote list as readable text (the FOOTNOTE branch gates it).
    from pdf2md.emit import _Ctx, _render_block
    from pdf2md.schema import Block, BlockType

    ctx = _Ctx(depth_of={}, tables={}, figures={})
    fn = Block(id="#/fn", type=BlockType.FOOTNOTE, text="❆ ♣/a114❛❝", page=1)
    footnotes: list[str] = []
    text, status, flag = _render_block(fn, ctx, footnotes)
    assert status == CoverageStatus.FLAGGED and "illegible text layer" in text
    assert footnotes == []  # not passed off as readable


def test_emit_snapshot(tmp_path, sample_document, snapshot):
    md_files, _ = _emit(tmp_path, sample_document)
    assert md_files[0].read_text() == snapshot


def test_heading_plan_dedup_and_merge():
    from pdf2md.emit import _heading_plan
    from pdf2md.schema import Block, BlockType

    blocks = [
        Block("#/h0", BlockType.HEADING, "Part I Overview of GRASP2018", 1),
        Block("#/h1", BlockType.HEADING, "Chapter 1", 1),
        Block("#/h2", BlockType.HEADING, "GRASP2018", 1),
        Block("#/h3", BlockType.HEADING, "1.1 Relativistic calculations", 1),
    ]
    skip, text = _heading_plan(blocks, "I Overview of GRASP2018")
    assert "#/h0" in skip                            # restates the file title -> dropped
    assert text["#/h1"] == "Chapter 1: GRASP2018"    # bare label merged with its title
    assert "#/h2" in skip                            # the title was consumed by the merge
    assert "#/h3" not in skip and "#/h3" not in text  # a normal numbered section is left alone


def test_heading_plan_label_plus_title_dup_dropped():
    # When a "Part N" label is followed by a heading that restates the file title,
    # both are dropped (the file title already says it), not merged into a duplicate.
    from pdf2md.emit import _heading_plan
    from pdf2md.schema import Block, BlockType

    blocks = [
        Block("#/h0", BlockType.HEADING, "Part IV", 1),
        Block("#/h1", BlockType.HEADING, "Issues of convergence and non-default options", 1),
    ]
    skip, text = _heading_plan(blocks, "IV Issues of convergence and non-default options")
    assert "#/h0" in skip and "#/h1" in skip and "#/h0" not in text


def test_section_refs_linkified_outside_fences(tmp_path):
    from pdf2md.emit import _link_refs

    p = tmp_path / "doc.md"
    p.write_text("---\ntitle: x\n---\n\nSee section 9.2 here. Also section 1.1.\n\n"
                 "```\nrun and read section 9.2 now\n```\n\nAnd section 7 (no dot).\n")
    smap = {"9.2": ("09_x.md", "92-foo"), "1.1": ("doc.md", "11-bar")}
    _link_refs(p, smap)
    out = p.read_text()
    assert "[section 9.2](09_x.md#92-foo)" in out   # cross-file link
    assert "[section 1.1](#11-bar)" in out          # same-file -> bare anchor
    assert "run and read section 9.2 now" in out    # inside a code fence: left verbatim
    assert "And section 7 (no dot)." in out         # bare number: not linked (ambiguous)


def test_illegible_prose_flagged_not_silently_emitted(tmp_path):
    # A prose block still symbol-font garbage after enrich's refill must surface as
    # a visible marker + an `illegible` tally, not pass as readable text — the exact
    # blind spot that let GRASP report lossless while 67% was dingbats.
    from pdf2md.schema import Block, BlockType, Document
    from pdf2md.structure import build_structure

    g = Block(id="#/texts/0", type=BlockType.PARAGRAPH, text="❆ ♣/a114❛❝/a116✐❝❛❧", page=1)
    structure = build_structure([g], None, title="Doc", page_count=1)
    doc = Document(
        doc_id="abc123def456789a", source_path="/x/Doc.pdf", source_sha256="abc123def456789a",
        version=1, page_count=1, sections=structure.root, blocks=[g], tables=[], figures=[],
    )
    md_files, flags = emit_document(doc, structure, tmp_path, {"title": "Doc"},
                                    {"docling": "2.93.0", "pdf2md": "0.1.0"})
    text = md_files[0].read_text()

    assert "[pdf2md: illegible text layer]" in text
    assert "❆" not in text                 # the garbage itself is not emitted as prose
    assert "illegible_blocks: 1" in text    # front-matter surfaces it
    assert g.coverage_status == CoverageStatus.FLAGGED
    report = build_report(doc.doc_id, doc.blocks, flags)
    assert report.illegible == 1 and report.lossless
