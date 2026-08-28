"""Document inventory and independent quality evidence from synthetic blocks."""

from __future__ import annotations

import json

from pdf2md.profile import (
    _confidence,
    build_profile,
    write_manifest,
    write_profile,
    write_readme,
)
from pdf2md.schema import (
    Block,
    BlockType,
    CoverageFlag,
    CoverageReport,
    CoverageStatus,
    Document,
    Digitization,
    FigureRef,
    TableData,
)
from pdf2md.structure import build_structure


def test_confidence_clean_is_high():
    grade, reasons = _confidence(accounted_for=True, illegible=0, ocr_pages=0, pages=10,
                                 equations=5, image_backed=0)
    assert grade == "high" and "clean" in reasons[0]


def test_confidence_scanned_is_medium():
    grade, reasons = _confidence(True, 0, 8, 10, 0, 0)
    assert grade == "medium" and any("scanned" in r for r in reasons)


def test_confidence_many_illegible_is_low():
    grade, reasons = _confidence(True, 12, 0, 10, 0, 0)
    assert grade == "low" and any("illegible" in r for r in reasons)


def test_confidence_incomplete_accounting_is_low():
    grade, _ = _confidence(False, 0, 0, 10, 0, 0)
    assert grade == "low"


def test_image_backed_equations_are_informational_not_a_downgrade():
    grade, reasons = _confidence(True, 0, 0, 10, 10, 7)
    assert grade == "high" and any("image-backed" in r for r in reasons)


def test_build_profile_inventory():
    blocks = [
        Block("#/p", BlockType.PARAGRAPH, "hello world", 1),
        Block("#/e", BlockType.EQUATION, "x", 2, extra={"crop_path": "a.png"}),
        Block("#/c", BlockType.CODE, "print(1)", 2),
        Block("#/o", BlockType.PARAGRAPH, "ocr text", 3, extra={"ocr": True}),
        Block("#/t", BlockType.TABLE, "", 2),  # a table has a block, paired with its TableData
    ]
    structure = build_structure(blocks, None, title="D", page_count=3)
    doc = Document("x" * 16, "/x.pdf", "x" * 16, 1, 3, structure.root, blocks=blocks,
                   figures=[FigureRef("#/f", 1, None)],
                   tables=[TableData(
                       "#/t",
                       1,
                       None,
                       gfm="| a |",
                       normalized_json_path="data/tables/page_001_panels.json",
                       cell_evidence_counts={"candidate": 2, "reader_agreement": 1},
                       cell_resolution_counts={"low": 2, "high": 1},
                   )])
    doc.coverage = CoverageReport("x", total_blocks=5, emitted=5, cropped=0, flagged=0,
                                  dropped=0, illegible=0)
    p = build_profile(doc)
    assert p.pages == 3 and p.blocks == 5
    assert p.equations == 1 and p.equations_image_backed == 1
    assert p.code_blocks == 1 and p.figures == 1 and p.tables == 1
    assert p.tables_verified == 1 and p.tables_candidates == 0 and p.tables_image_only == 0
    assert p.derived_table_datasets == 1
    assert p.table_cell_evidence == {"candidate": 2, "reader_agreement": 1}
    assert p.table_cell_resolution == {"low": 2, "high": 1}
    assert p.ocr_pages == 1
    assert p.accounted_for and p.complete and not p.needs_review
    assert p.encoding_legibility == 1.0
    assert p.confidence == "medium"
    # sufficiency: 3 prose/code blocks + the GFM table are text-sufficient; the image-backed
    # equation and the data-less figure are pixel-authoritative.
    assert p.text_sufficient == 4
    assert p.pixel_authoritative == 2
    assert p.pixel_authoritative_by == {"image-only figures": 1, "image-backed equations": 1}


def test_glyph_unbacked_table_counts_as_candidate():
    # A vision-read raster table (cells_unverified, no ocr flag) rides beside its
    # authoritative crop like a scanned table: candidate, not verified.
    block = Block("#/tables/0", BlockType.TABLE, "", 1,
                  extra={"cells_unverified": True, "crop_path": "assets/t.png"})
    block.coverage_status = CoverageStatus.CROPPED
    structure = build_structure([block], None, title="D", page_count=1)
    doc = Document("x" * 64, "/x.pdf", "x" * 64, 1, 1, structure.root,
                   blocks=[block],
                   tables=[TableData(block.id, 1, None,
                                     gfm="| a |\n|---|\n| b |")])
    doc.coverage = CoverageReport(doc.doc_id, total_blocks=1, emitted=0, cropped=1,
                                  flagged=0, dropped=0)

    profile = build_profile(doc)

    assert profile.tables_candidates == 1 and profile.tables_verified == 0
    assert profile.pixel_authoritative_by.get("image-backed tables") == 1


def test_rejected_chart_data_remains_pixel_authoritative():
    block = Block("#/f", BlockType.FIGURE, "", 1)
    structure = build_structure([block], None, title="D", page_count=1)
    doc = Document(
        "x" * 64,
        "/x.pdf",
        "x" * 64,
        1,
        1,
        structure.root,
        blocks=[block],
        figures=[FigureRef(
            block.id,
            1,
            None,
            asset_path="assets/figure.png",
            digitization=Digitization(
                series=[[(0.0, 1.0)]],
                method="vlm-estimated",
                confidence=0.3,
                note="uncertain",
            ),
        )],
    )

    profile = build_profile(doc)

    assert profile.text_sufficient == 0
    assert profile.pixel_authoritative_by == {"image-only figures": 1}


def test_write_profile_and_readme(tmp_path):
    blocks = [Block("#/p", BlockType.PARAGRAPH, "hello", 1)]
    structure = build_structure(blocks, None, title="Doc", page_count=1)
    doc = Document("abcd" * 4, "/x/Manual.pdf", "abcd" * 4, 1, 1, structure.root, blocks=blocks)
    doc.coverage = CoverageReport("x", total_blocks=1, emitted=1, cropped=0, flagged=0,
                                  dropped=0, illegible=0)
    profile = build_profile(doc)
    md_files = [tmp_path / "index.md", tmp_path / "01_intro.md"]

    write_profile(tmp_path, doc, profile, md_files)
    run_metrics = {
        "duration_s": 65.0,
        "stages": {
            "parse": {"duration_s": 60.0, "counts": {"pages": 1}},
            "charts": {
                "duration_s": 5.0,
                "counts": {
                    "enabled": True,
                    "attempted": 4,
                    "accepted": 1,
                    "declined": 2,
                    "failed": 1,
                    "ocr_axis_attempted": 2,
                    "ocr_axis_ineligible": 1,
                    "vision_cache_lookups": 3,
                    "vision_cache_hits": 2,
                    "vision_cache_misses": 1,
                    "vision_cache_writes": 1,
                },
            },
            "descriptions": {
                "duration_s": 0.0,
                "counts": {"vision_failures": 2},
            },
        },
        "memory": {
            "available": True,
            "scope": "process-lifetime high-water marks",
            "main_process_peak_rss_bytes": 128 * 1024**2,
            "largest_terminated_child_peak_rss_bytes": 64 * 1024**2,
        },
    }
    metadata_evidence = {
        "schema_version": 1,
        "method": "ranked-local-metadata-v1",
        "title": {
            "selected": {
                "value": "The Manual", "score": 94, "quality": "high",
                "evidence": [{"source": "front_heading", "page": 1}],
                "penalties": [],
            },
            "alternatives": [],
            "rejected": [],
        },
        "authors": {"selected": None, "alternatives": [], "rejected": []},
    }
    write_readme(
        tmp_path,
        doc,
        {"title": "The Manual", "metadata_evidence": metadata_evidence},
        profile,
        md_files,
        run_metrics=run_metrics,
    )

    pj = json.loads((tmp_path / "profile.json").read_text())
    assert pj["confidence"] == "high" and pj["confidence_deprecated"]
    assert pj["contents"] == "index.md"
    assert "quality_scorecard" in pj
    assert pj["files"] == ["index.md", "01_intro.md"] and pj["source"] == "Manual.pdf"
    assert pj["source_sha256"] == "abcd" * 4
    readme = (tmp_path / "README.md").read_text()
    assert "## Quality scorecard" in readme and "Where to start" in readme
    assert "## Bibliographic metadata" in readme
    assert "Title: The Manual. Evidence quality: high; source(s): front_heading." in readme
    assert "Authors: no local candidate selected." in readme
    assert "Legacy aggregate label: high (deprecated and uncalibrated)." in readme
    assert "The Manual" in readme and "index.md" in readme
    assert "Structural representation complete: yes." in readme
    assert "Action required: no (0 item(s))." in readme
    assert "Source-dependent entries: 0." in readme
    assert "Recorded wall time: 1m 05s." in readme
    assert "Charts: 4 attempted, 1 accepted, 2 declined, 1 failed" in readme
    assert "Vision cache: 2 of 3 lookups served from cache" in readme
    assert "Optional model work is partial: 2 call(s) failed after retries." in readme
    assert "rerunning the same command retries missing regions" in readme
    assert "Main-process peak RSS: 128.0 MiB" in readme
    assert "Largest terminated child peak RSS: 64.0 MiB" in readme


def test_scorecard_keeps_image_backed_equation_structurally_complete():
    block = Block(
        "#/e",
        BlockType.EQUATION,
        "unverified hint",
        1,
        extra={"crop_path": "assets/equation.png"},
    )
    block.coverage_status = CoverageStatus.CROPPED
    structure = build_structure([block], None, title="D", page_count=1)
    doc = Document("x" * 64, "/x.pdf", "x" * 64, 1, 1, structure.root, blocks=[block])
    doc.coverage = CoverageReport(doc.doc_id, 1, 0, 1, 0, 0)

    profile = build_profile(doc, metadata={"title": "D"})
    dimensions = profile.quality_scorecard["dimensions"]

    assert dimensions["structural_completeness"]["status"] == "complete"
    assert dimensions["structural_completeness"]["ratio"] == 1.0
    assert dimensions["equation_text_coverage"]["status"] == "none"
    assert dimensions["equation_text_coverage"]["ratio"] == 0.0
    assert profile.confidence == "medium"


def test_scorecard_surfaces_illegible_prose_as_unresolved_error():
    block = Block("#/p", BlockType.PARAGRAPH, "unreadable", 1)
    block.coverage_status = CoverageStatus.FLAGGED
    structure = build_structure([block], None, title="D", page_count=1)
    doc = Document("x" * 64, "/x.pdf", "x" * 64, 1, 1, structure.root, blocks=[block])
    doc.coverage = CoverageReport(doc.doc_id, 1, 0, 0, 1, 0, illegible=1)

    dimensions = build_profile(doc).quality_scorecard["dimensions"]

    unresolved = dimensions["unresolved_error_severity"]
    assert unresolved["status"] == "high"
    assert unresolved["counts"]["illegible_prose_blocks"] == 1


def test_every_scorecard_dimension_names_evidence_and_calibration():
    block = Block("#/p", BlockType.PARAGRAPH, "hello", 1)
    structure = build_structure([block], None, title="D", page_count=1)
    doc = Document("x" * 64, "/x.pdf", "x" * 64, 1, 1, structure.root, blocks=[block])
    doc.coverage = CoverageReport(doc.doc_id, 1, 1, 0, 0, 0)
    engine_quality = {
        "source": "Docling ConversionResult.confidence",
        "calibrated": False,
        "grades": {"parse": "excellent", "layout": "good", "ocr": "fair"},
        "raw_scores": {"parse": 0.95, "layout": 0.85, "ocr": 0.75},
    }

    profile = build_profile(
        doc,
        metadata={"title": "D", "authors": ["A. Author"]},
        engine_quality=engine_quality,
    )

    for dimension in profile.quality_scorecard["dimensions"].values():
        assert dimension["evidence_source"]
        assert dimension["calibrated"] is False
    assert profile.quality_scorecard["dimensions"]["layout_quality"]["status"] == "good"
    assert profile.quality_scorecard["dimensions"]["ocr_quality"]["status"] == "fair"
    assert profile.quality_scorecard["engine_evidence"]["grades"]["parse"] == "excellent"


def test_write_manifest_maps_navigation_assets_and_review(tmp_path):
    blocks = [
        Block("#/p", BlockType.PARAGRAPH, "hello", 1),
        Block(
            "#/e",
            BlockType.EQUATION,
            "E = mc^2",
            2,
            extra={"crop_path": "assets/equation.png"},
        ),
        Block("#/f", BlockType.FIGURE, "", 2),
    ]
    for block in blocks:
        block.coverage_status = CoverageStatus.CROPPED
    structure = build_structure(blocks, None, title="Doc", page_count=2)
    doc = Document(
        "abcd" * 16,
        "/x/Manual.pdf",
        "abcd" * 16,
        3,
        2,
        structure.root,
        blocks=blocks,
        figures=[FigureRef("#/f", 2, None, asset_path="assets/figure.png")],
    )
    flag = CoverageFlag("#/e", 2, "equation image fallback", "marker")
    doc.coverage = CoverageReport(
        doc.doc_id,
        total_blocks=3,
        emitted=0,
        cropped=3,
        flagged=0,
        dropped=0,
        flags=[flag],
    )
    profile = build_profile(doc)

    metadata_evidence = {
        "schema_version": 1,
        "method": "ranked-local-metadata-v1",
        "title": {
            "selected": {
                "value": "The Manual", "score": 94, "quality": "high",
                "evidence": [{"source": "embedded_title"}], "penalties": [],
            },
            "alternatives": [],
            "rejected": [],
        },
        "authors": {"selected": None, "alternatives": [], "rejected": []},
    }
    write_manifest(
        tmp_path,
        doc,
        {"title": "The Manual", "metadata_evidence": metadata_evidence},
        profile,
        [tmp_path / "index.md", tmp_path / "01_intro.md"],
        {2: "assets/page_002.png"},
        passage_count=7,
    )

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["document"] == {
        "id": "abcd" * 16,
        "version": 3,
        "title": "The Manual",
        "pages": 2,
    }
    assert manifest["source"] == {
        "path": "../source.pdf",
        "sha256": "abcd" * 16,
    }
    assert manifest["metadata"] == {
        "selected": {
            "title": "The Manual",
            "authors": None,
            "year": None,
            "doi": None,
            "venue": None,
        },
        "evidence": metadata_evidence,
    }
    assert manifest["read"]["start"] == "index.md"
    assert manifest["read"]["passages"] == "passages.jsonl"
    assert manifest["read"]["passage_schema"] == "passages.schema.json"
    assert manifest["read"]["outline"] == "outline.json"
    assert manifest["read"]["symbols"] == "symbols.json"
    assert manifest["inventory"]["passages"] == 7
    assert manifest["representations"]["figures"] == [{
        "block_id": "#/f",
        "page": 2,
        "image": "assets/figure.png",
        "svg": None,
        "data": None,
        "code": None,
        "has_structured_data": False,
        "data_extraction_status": "not_attempted",
        "data_extraction_note": None,
    }]
    assert manifest["representations"]["equations"] == [{
        "block_id": "#/e",
        "page": 2,
        "representation": "image_with_text_hint",
        "crop": "assets/equation.png",
    }]
    assert manifest["review"] == [{
        "disposition": "action_required",
        "severity": "medium",
        "content_impact": "medium",
        "content_type": "equation",
        "block_id": "#/e",
        "page": 2,
        "reason": "equation image fallback",
        "asset": "assets/equation.png",
        "source_page": "../source.pdf#page=2",
    }]
    assert manifest["read"]["review"] == "review.md"
    assert manifest["read"]["review_queue"] == "review.json"


def test_confidence_reports_partial_vlm_page_coverage():
    from pdf2md.profile import _confidence

    # all scanned pages transcribed by the VLM -> plain claim
    _, full = _confidence(True, 0, 14, 14, 4, 4, ocr_by_vlm=True, vlm_pages=14)
    assert any("OCR by a vision model" in r and "pages," not in r for r in full)

    # only some pages transcribed (the rest fell back to engine OCR) -> honest partial claim
    _, partial = _confidence(True, 0, 14, 14, 4, 4, ocr_by_vlm=True, vlm_pages=8)
    assert any("8/14 pages" in r and "engine OCR on the rest — verify" in r for r in partial)


def test_manifest_distinguishes_scanned_table_candidate_from_authority(tmp_path):
    block = Block(
        "#/table",
        BlockType.TABLE,
        "",
        1,
        extra={"ocr": True, "crop_path": "assets/table.png"},
    )
    block.coverage_status = CoverageStatus.CROPPED
    structure = build_structure([block], None, title="D", page_count=1)
    table = TableData(
        block.id,
        1,
        None,
        "| A |\n|---|\n| 1 |",
        candidate_path="data/tables/table.md",
        data_path="data/tables/table.csv",
        json_path="data/tables/table.json",
    )
    doc = Document(
        "x" * 64,
        "/source.pdf",
        "x" * 64,
        1,
        1,
        structure.root,
        blocks=[block],
        tables=[table],
    )
    doc.coverage = CoverageReport(
        doc.doc_id,
        1,
        0,
        1,
        0,
        0,
        flags=[CoverageFlag(block.id, 1, "table image fallback", "marker")],
    )
    profile = build_profile(doc)

    write_manifest(tmp_path, doc, {"title": "D"}, profile, [tmp_path / "document.md"], {})

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert profile.tables_candidates == 1 and profile.tables_verified == 0
    assert manifest["representations"]["tables"] == [{
        "block_id": block.id,
        "page": 1,
        "representation": "image_with_ocr_candidate",
        "authority": "image",
        "crop": "assets/table.png",
        "candidate": "data/tables/table.md",
        "csv": "data/tables/table.csv",
        "json": "data/tables/table.json",
        "normalized_csv": None,
        "normalized_json": None,
            "cell_evidence": None,
            "cell_evidence_counts": {},
            "cell_resolution_counts": {},
        }]

    block.extra.pop("crop_path")
    write_manifest(tmp_path, doc, {"title": "D"}, profile, [tmp_path / "document.md"], {})
    table_record = json.loads((tmp_path / "manifest.json").read_text())["representations"]["tables"][0]
    assert table_record["representation"] == "ocr_candidate_without_crop"
    assert table_record["authority"] == "image" and table_record["crop"] is None


def test_profile_surfaces_review_markers():
    blocks = [Block("#/e", BlockType.EQUATION, "x", 1)]
    blocks[0].coverage_status = CoverageStatus.CROPPED
    structure = build_structure(blocks, None, title="D", page_count=1)
    doc = Document("x" * 64, "/x.pdf", "x" * 64, 1, 1, structure.root, blocks=blocks)
    flag = CoverageFlag(
        "#/e", 1, "equation: image is authoritative", "marker",
        disposition="source_dependent", severity="none", content_impact="low",
    )
    doc.coverage = CoverageReport("x", 1, 0, 1, 0, 0, flags=[flag])

    profile = build_profile(doc)

    assert profile.accounted_for and profile.complete and not profile.needs_review
    assert profile.review_flags == 1
    assert profile.review_reasons == {"equation: image is authoritative": 1}
    assert any("1 review marker(s)" in reason for reason in profile.confidence_reasons)


def test_profile_defaults_when_consistency_not_computed():
    blocks = [Block("#/p", BlockType.PARAGRAPH, "hello", 1)]
    structure = build_structure(blocks, None, title="D", page_count=1)
    doc = Document("x" * 64, "/x.pdf", "x" * 64, 1, 1, structure.root, blocks=blocks)
    doc.coverage = CoverageReport("x", total_blocks=1, emitted=1, cropped=0, flagged=0,
                                  dropped=0, illegible=0)

    p = build_profile(doc)

    assert p.glyph_recall_blocks == 0
    assert p.numeric_conservation == {"available": False, "reason": "not computed"}


def test_profile_carries_token_consistency(tmp_path):
    healthy = Block("#/p1", BlockType.PARAGRAPH, "fine text", 1,
                    extra={"glyph_word_recall": {"matched": 98, "total": 100}})
    lossy = Block("#/p2", BlockType.PARAGRAPH, "short", 2,
                  extra={"glyph_word_recall": {"matched": 4, "total": 10}})
    structure = build_structure([healthy, lossy], None, title="D", page_count=2)
    doc = Document("x" * 64, "/x.pdf", "x" * 64, 1, 2, structure.root,
                   blocks=[healthy, lossy])
    doc.coverage = CoverageReport("x", total_blocks=2, emitted=2, cropped=0, flagged=0,
                                  dropped=0, illegible=0)
    consistency = {
        "available": True,
        "source_values": 50, "conserved_values": 48, "missing_values": 2,
        "missing_examples": [{"value": "92.4", "count": 1}],
        "pages_with_text_layer": 2, "scan_pages_skipped": 0,
        "representation_aware": {
            "categories": {
                "unexplained_loss": {"words": 1, "numbers": 0},
                "unexplained_addition": {"words": 0, "numbers": 1},
                "expected_source_dependent": {"words": 3, "numbers": 2},
            }
        },
    }

    p = build_profile(doc, consistency=consistency)
    write_readme(tmp_path, doc, {"title": "D"}, p, [tmp_path / "document.md"])

    assert p.glyph_recall_blocks == 2 and p.glyph_recall_words_total == 110
    assert p.glyph_recall_words_matched == 102 and p.glyph_low_recall_blocks == 1
    assert p.numeric_conservation["conserved_values"] == 48
    readme = (tmp_path / "README.md").read_text()
    assert "word recall: 102 of 110 word(s) across 2 block(s); 1 below 90%" in readme
    assert "Numeric conservation: 48 of 50 numeric value(s)" in readme
    assert "2 missing (examples in `profile.json`)." in readme
    assert "1 unexplained word loss(es), 0 unexplained number loss(es)" in readme
    assert "0 unexplained word addition(s), and 1 unexplained number addition(s)" in readme
