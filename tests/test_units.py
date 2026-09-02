"""Small pure-logic units: tables, cache, coverage, metadata."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

from pdf2md.cache import content_hash, doc_dir, document_slug, latest_version, next_version
from pdf2md.coverage import build_report
from pdf2md.schema import Block, BlockType, CoverageStatus, TableData
from pdf2md.tables import render_table


def test_progress_reports_elapsed_remaining_and_eta(caplog, monkeypatch):
    from pdf2md.logging import Progress

    clock = iter((0.0, 0.0, 120.0))
    monkeypatch.setattr("pdf2md.logging.time.monotonic", lambda: next(clock))
    progress = Progress(logging.getLogger("pdf2md.test"))

    with caplog.at_level(logging.INFO, logger="pdf2md.test"):
        progress.count("verifying tables", 1, 4, unit="tables", force=True)
        progress.count("verifying tables", 2, 4, unit="tables", force=True)

    assert caplog.messages == [
        "progress 0s | verifying tables: 1/4 tables (25%, 3 remaining)",
        "progress 2m 00s | verifying tables: 2/4 tables "
        "(50%, 2 remaining, ETA 2m 00s)",
    ]


def test_progress_accepts_a_counter_reset_for_the_next_batch(caplog, monkeypatch):
    from pdf2md.logging import Progress

    clock = iter((0.0, 10.0, 11.0))
    monkeypatch.setattr("pdf2md.logging.time.monotonic", lambda: next(clock))
    progress = Progress(logging.getLogger("pdf2md.test"))

    with caplog.at_level(logging.INFO, logger="pdf2md.test"):
        progress.count("MinerU Layout Predict", 10, 10, unit="items")
        progress.count("MinerU Layout Predict", 1, 10, unit="items")

    assert "10/10 items" in caplog.messages[0]
    assert "1/10 items" in caplog.messages[1]


def test_progress_heartbeat_reports_blocking_stage(caplog):
    from pdf2md.logging import Progress

    logger = logging.getLogger("pdf2md.test.heartbeat")
    progress = Progress(logger)
    with caplog.at_level(logging.INFO, logger=logger.name):
        with progress.heartbeat("still reading source with docling", interval_seconds=0.005):
            time.sleep(0.015)

    assert any("still reading source with docling" in message for message in caplog.messages)


def test_repeated_third_party_warnings_keep_first_and_report_exact_count(caplog):
    from pdf2md.logging import collapse_repeated_warnings

    source = logging.getLogger("third-party.test.repeats")
    reporter = logging.getLogger("pdf2md.test.warning-summary")
    with caplog.at_level(logging.WARNING):
        with collapse_repeated_warnings(
            (source.name,), report_to=reporter, stage="source read"
        ) as warnings:
            source.warning("empty OCR result")
            source.warning("empty OCR result")
            source.warning("different warning")
            source.warning("empty OCR result")

    assert caplog.messages == [
        "empty OCR result",
        "different warning",
        "source read: suppressed 2 repeated warning(s) from "
        "third-party.test.repeats: empty OCR result",
    ]
    assert len(warnings.counts) == 2
    assert warnings.repeat_count == 2


def test_expand_ligature_glyphs():
    # A broken TeX font surfaces its f-ligatures and discretionary hyphen as C0 control
    # bytes; clean_reading must expand them to letters, not strip them to spaces.
    from pdf2md.normalize import clean_reading, expand_ligature_glyphs

    assert clean_reading("the \x1crst con\x1cguration \x1cles") == "the first configuration files"
    assert clean_reading("di\x1berent e\x1ecient \x1doating") == "different efficient floating"
    # The same C0 slots mean other things in other TeX fonts: 0x1C is `fi` in an
    # OT1 text font and what cmsy draws for `\ll`. A ligature is always inside a
    # word, so a byte alone between spaces is left for the control strip rather
    # than turning `Normally M << N` into `Normally M fi N`.
    assert clean_reading("Normally M \x1c N.") == "Normally M N."
    assert expand_ligature_glyphs("\x1c") == "\x1c"
    assert clean_reading("practi\x02cal") == "practical"  # soft hyphen -> join
    assert clean_reading("normal clean text") == "normal clean text"  # untouched


def test_dpi_for_region_clamps_small_up_and_large_down():
    from pdf2md.render import dpi_for_region
    from pdf2md.schema import BBox

    # A small dense region (100x40 pts, long side 1.39 in) wants 1600/1.39 ~= 1152 dpi,
    # clamped to the 600 ceiling — sub/superscripts get maximum sharpness.
    small = BBox(x0=0, y0=40, x1=100, y1=0)
    assert dpi_for_region(small, target_px=1600, floor=220) == 600

    # A full-column region (500x700 pts) wants 1600/9.72 ~= 165 dpi, clamped up to the
    # 220 floor — no point re-rendering; the model downsamples it anyway.
    large = BBox(x0=0, y0=700, x1=500, y1=0)
    assert dpi_for_region(large, target_px=1600, floor=220) == 220

    # Bottom-left-origin bbox (y0 > y1) measures the same as top-left.
    flipped = BBox(x0=10, y0=250, x1=210, y1=180)  # 200x70 pts, long side 2.78 in
    assert dpi_for_region(flipped, target_px=1600, floor=220) == 576


def test_scanned_block_crop_keeps_300_dpi_source_detail():
    from pdf2md.config import Config
    from pdf2md.pipeline import _block_crop_dpi
    from pdf2md.schema import BBox, Block, BlockType

    bbox = BBox(x0=0, y0=700, x1=500, y1=0)
    scanned = Block("#/table", BlockType.TABLE, "", 1, bbox=bbox, extra={"ocr": True})
    digital = Block("#/equation", BlockType.EQUATION, "", 1, bbox=bbox)

    assert _block_crop_dpi(scanned, Config()) == 300
    assert _block_crop_dpi(digital, Config()) == 220


def test_collapse_repeats_truncates_a_loop_not_structured_content():
    from pdf2md.describe import collapse_repeats

    # A generation loop re-emits the same line many times -> keep one copy, report the cut.
    loop = "\n".join(["1 A. H. Compton, Phys. Rev., 22:409 (1923)."] * 40)
    text, cut = collapse_repeats(loop)
    assert cut and text == "1 A. H. Compton, Phys. Rev., 22:409 (1923)."

    # Similar-but-DISTINCT rows (a scanned TOC/table) must NOT be treated as a loop or
    # dropped -- this is the lossless-invariant regression the review caught.
    toc = "\n".join(f"Chapter {i} .......... {i * 4}" for i in range(1, 9))
    assert collapse_repeats(toc) == (toc, False)

    prose = "First line about wave mechanics.\nA second, entirely different sentence."
    assert collapse_repeats(prose) == (prose, False)


def test_collapse_repeats_truncates_a_repeated_page_section():
    from pdf2md.describe import collapse_repeats

    seed = [
        r"$$m \frac{d^2q}{dt^2} = -kq$$ (1-25)",
        "whose solution is a sinusoidal oscillation with a frequency nu, where",
        r"$$nu = \frac{1}{2pi} \sqrt{\frac{k}{m}}$$ (1-26)",
    ]
    first = ["Introductory paragraph.", *seed, "First complete continuation."]
    repeated = [*seed, "Looped continuation.", *seed, "Looped continuation."]
    text, cut = collapse_repeats("\n\n".join([*first, *repeated]))

    assert cut is True
    assert text == "\n\n".join(first)


def test_plot_data_withholds_numbers_below_confidence_floor():
    from pdf2md.emit import _plot_data
    from pdf2md.schema import Digitization

    hi = Digitization(series=[[(0.0, 1.0), (1.0, 2.0)]], method="vector-path", confidence=0.9,
                      note="n", y_kind="log")
    out = _plot_data(hi)
    assert "```csv" in out and "0.0,1.0" in out  # trusted -> numbers printed
    # the axis scale rides with the data, and a runnable repro script sits beside it
    assert "# x scale: linear" in out and "# y scale: log" in out
    assert "```python" in out and "ax.set_yscale('log')" in out

    lo = Digitization(series=[[(0.0, 1.0)]], method="vlm-estimated", confidence=0.3, note="n")
    out = _plot_data(lo)
    # low trust -> numbers WITHHELD (data AND script) so they can't be scraped past the flag
    assert "data withheld" in out and "```csv" not in out and "```python" not in out
    assert "0.0,1.0" not in out

    # a gated read (the raster pre-scan vetoed digitization) says why, with no data block
    gated = Digitization(series=[], method="raster-gated", confidence=0.0,
                         note="too tangled for a trustworthy automated read")
    out = _plot_data(gated)
    assert "not extracted" in out and "too tangled" in out and "```" not in out


def test_plot_data_points_at_the_printed_table():
    from pdf2md.emit import _plot_data
    from pdf2md.schema import Digitization, FigureLabels

    # no digitization at all (default config on a scan): the pointer still lands
    out = _plot_data(None, caption="Fig 2. Locations listed in Table 5.")
    assert "Table 5" in out and "authoritative data" in out

    # Ghia-style: the reference lives in the OCR'd labels, run together ("Table5")
    out = _plot_data(None, None, FigureLabels("(Locations of p.v.c\nListed in Table5)", 0.55, "n"))
    assert "Table 5" in out

    # withheld numbers -> pointer; printed numbers -> no pointer (the CSV is the data)
    lo = Digitization(series=[[(0.0, 1.0)]], method="vlm-estimated", confidence=0.2, note="n")
    assert "Table 3" in _plot_data(lo, caption="See Table 3.")
    hi = Digitization(series=[[(0.0, 1.0)]], method="vector-path", confidence=0.99, note="n")
    assert "authoritative data" not in _plot_data(hi, caption="See Table 3.")

    assert _plot_data(None, caption="No reference here.") == ""


def test_plot_script_by_kind_with_figure_context():
    from pdf2md.emit import _plot_data
    from pdf2md.schema import Digitization, FigureLabels

    bars = Digitization(series=[[(1.0, 3.0), (2.0, 7.0)]], method="vector-path",
                        confidence=0.99, note="n", kind="bar")
    out = _plot_data(bars, caption="Fig 3.  Counts per\n condition.",
                     labels=FigureLabels("Counts\nCondition", 0.9, "n"))
    assert "ax.bar(xs, ys" in out and "ax.plot" not in out
    # the figure's own printed text rides in the script as comments, never as guesses
    assert "# caption: Fig 3. Counts per condition." in out
    assert "#   Condition" in out

    dots = Digitization(series=[[(0.0, 1.0)]], method="vector-path",
                        confidence=0.99, note="n", kind="scatter")
    out = _plot_data(dots)
    assert "linestyle='none'" in out and "ax.bar" not in out


def test_clean_vlm_text_strips_stray_code_fence():
    from pdf2md.describe import clean_vlm_text

    # glm-ocr wraps a tiny OCR result in a dangling markdown fence despite the prompt.
    assert clean_vlm_text("center.\n```markdown") == ("center.", False)
    assert clean_vlm_text("```\nplain text\n```") == ("plain text", False)


def test_vector_digitizer_recovers_known_plot():
    from pathlib import Path

    import pypdfium2 as pdfium

    from pdf2md.digitize import VectorPathDigitizer
    from pdf2md.schema import BBox

    # tests/fixtures/vector_plot.pdf is a born-digital plot of y = x^2 for x = 0..5.
    pdf_path = Path(__file__).parent / "fixtures" / "vector_plot.pdf"
    doc = pdfium.PdfDocument(str(pdf_path))
    w, h = doc[0].get_size()
    doc.close()

    result = VectorPathDigitizer().digitize(pdf_path, 1, BBox(x0=0, y0=0, x1=w, y1=h))
    assert result is not None and result.method == "vector-path"
    assert result.confidence > 0.95
    (series,) = result.series
    ys = [y for _, y in series]
    # near-lossless: reading the drawn coordinates, not tracing pixels
    for got, want in zip(ys, [0, 1, 4, 9, 16, 25]):
        assert abs(got - want) < 0.1


def test_figure_digitization_reuses_one_document_and_one_page_handle(tmp_path, monkeypatch):
    from pdf2md import visual
    from pdf2md.config import Config
    from pdf2md.schema import BBox, FigureRef

    opened = []
    pages_read = []

    class FakePdf:
        def __init__(self, path):
            opened.append(path)
            self.closed = False

        def __getitem__(self, index):
            pages_read.append(index)
            return f"page-{index + 1}"

        def close(self):
            self.closed = True

    calls = []

    class FakeDigitizer:
        def digitize_page_with_geometry(self, page, bbox):
            calls.append((page, bbox))
            return None, None

        def has_raster_image(self, page, bbox):
            return False

    progress_calls = []

    class FakeProgress:
        def count(self, label, completed, total, *, unit, detail=None):
            progress_calls.append((label, completed, total, unit, detail))

    monkeypatch.setattr(visual.pdfium, "PdfDocument", FakePdf)
    monkeypatch.setattr(visual, "VectorPathDigitizer", FakeDigitizer)

    bbox = BBox(0, 10, 10, 0)
    figures = [
        FigureRef("#/pictures/1", 1, bbox),
        FigureRef("#/pictures/2", 1, bbox),
        FigureRef("#/pictures/3", 2, bbox),
    ]
    counts = visual._digitize_figures(
        figures,
        tmp_path / "book.pdf",
        Config(),
        None,
        tmp_path / "v1",
        progress=FakeProgress(),
    )

    assert opened == [str(tmp_path / "book.pdf")]
    assert pages_read == [0, 1]
    assert [page for page, _ in calls] == ["page-1", "page-1", "page-2"]
    assert progress_calls[-1] == (
        "digitizing figures",
        3,
        3,
        "figures",
        "0 recovered, 0 OCR-axis attempts, 0 geometrically ineligible",
    )
    assert counts == {
        "attempted": 3,
        "accepted": 0,
        "declined": 3,
        "failed": 0,
        "ocr_axis_attempted": 0,
        "ocr_axis_ineligible": 0,
    }
    assert {figure.data_extraction_status for figure in figures} == {
        "no_chart_geometry"
    }


def test_ocr_axis_gate_requires_geometry_that_can_produce_a_series():
    from pdf2md.digitize import _VectorGeometry, _has_series_geometry

    frame = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    curve = [(0, 1), (5, 6), (10, 9)]
    assert _has_series_geometry(_VectorGeometry((0, 10, 0, 10), [frame, curve], [frame]))

    markers = [
        (2, 3, 0.5, 0.5, ("circle",)),
        (5, 6, 0.5, 0.5, ("circle",)),
        (8, 4, 0.5, 0.5, ("circle",)),
    ]
    assert _has_series_geometry(
        _VectorGeometry((0, 10, 0, 10), [frame], [frame], forms=markers)
    )

    bars = [
        [(1, 0), (3, 0), (3, 4), (1, 4), (1, 0)],
        [(5, 0), (7, 0), (7, 8), (5, 8), (5, 0)],
    ]
    assert _has_series_geometry(
        _VectorGeometry((0, 10, 0, 10), [frame, *bars], [frame])
    )

    flat_gridline = [(0, 5), (10, 5)]
    assert not _has_series_geometry(
        _VectorGeometry((0, 10, 0, 10), [frame, flat_gridline], [frame])
    )


def test_vector_inspection_distinguishes_embedded_raster_from_vector_plot(tmp_path):
    import pypdfium2 as pdfium
    from PIL import Image

    from pdf2md.digitize import VectorPathDigitizer
    from pdf2md.schema import BBox

    raster_pdf = tmp_path / "raster.pdf"
    Image.new("RGB", (200, 150), "gray").save(raster_pdf)
    digitizer = VectorPathDigitizer()
    cases = [
        (raster_pdf, True),
        (Path(__file__).parent / "fixtures" / "vector_plot.pdf", False),
    ]
    for source, expected in cases:
        document = pdfium.PdfDocument(str(source))
        try:
            page = document[0]
            width, height = page.get_size()
            assert digitizer.has_raster_image(
                page, BBox(0, height, width, 0)
            ) is expected
        finally:
            document.close()


def test_mixed_panel_kind_ties_are_deterministic():
    from pdf2md.digitize import _dominant_kind

    assert _dominant_kind(["bar", "line"]) == "line"
    assert _dominant_kind(["scatter", "bar", "bar"]) == "bar"


def test_vlm_digitize_parses_series_json():
    from pathlib import Path

    from pdf2md.digitize import _extract_json, vlm_digitize

    # tolerates a code fence and stray prose around the JSON
    raw = ('Sure:\n```json\n{"x_axis":"t","y_axis":"v",'
           '"series":[{"label":"a","points":[[0,1],[1,2.5]]}]}\n```')
    assert _extract_json(raw)["series"][0]["points"] == [[0, 1], [1, 2.5]]

    class Fake:
        def __init__(self, reply):
            self.reply = reply

        def describe(self, *a, **k):
            return self.reply

    d = vlm_digitize(Path("x.png"), Fake(raw))
    assert d.method == "vlm-estimated" and d.confidence < 0.5  # rough estimate, flagged
    assert d.series == [[(0.0, 1.0), (1.0, 2.5)]]

    # no readable data -> None, so the figure stays a crop rather than getting junk
    assert vlm_digitize(Path("x.png"), Fake('{"series": []}')) is None

    # series entries that aren't objects (a model quirk the live eval hit) don't raise
    assert vlm_digitize(Path("x.png"), Fake('{"series": ["a", 3]}')) is None
    assert vlm_digitize(Path("x.png"), Fake('{"series": "none"}')) is None

    # a looping reply repeats the whole object; the first balanced object is the read
    looped = '{"series":[{"label":"a","points":[[0,1]]}]}\n' * 3
    assert _extract_json(looped)["series"][0]["points"] == [[0, 1]]


def test_vlm_digitize_salvages_malformed_looping_reply():
    """glm-ocr's real failure shape (2026-07 bake-off): a one-character JSON slip
    ({label" for {"label") repeated in a loop. The points arrays are still clean —
    salvage them, once, and say so in the note."""
    from pathlib import Path

    from pdf2md.digitize import vlm_digitize

    class Fake:
        def __init__(self, reply):
            self.reply = reply

        def describe(self, *a, **k):
            return self.reply

    block = '```json\n{"series": [{label": "...", "points": [[0, 100], [2, 60.6]]}]}\n```\n'
    d = vlm_digitize(Path("x.png"), Fake(block * 3))
    assert d is not None
    assert d.series == [[(0.0, 100.0), (2.0, 60.6)]]  # deduped: one series, not three
    assert "recovered from a malformed model reply" in d.note


def test_vlm_digitize_anchored_to_pixel_calibration():
    from pathlib import Path

    from pdf2md.calibrate import RasterCalibration
    from pdf2md.digitize import vlm_digitize

    class Fake:
        def __init__(self, reply):
            self.reply, self.context = reply, None

        def describe(self, path, kind, context="", **k):
            self.context = context
            return self.reply

    cal = RasterCalibration((0.0, 2.0), (0.0, 5.0), "linear", "log", 0.99, 3, 0.9)
    raw = '{"series":[{"label":"a","points":[[0,1],[1,2.5],[2,4]]}]}'

    fake = Fake(raw)
    d = vlm_digitize(Path("x.png"), fake, cal)
    assert "x runs 0 to 2" in fake.context  # the measured axes rode into the prompt
    assert d.method == "vlm-anchored"
    assert d.x_kind == "linear" and d.y_kind == "log"  # calibration's scale rides out
    # anchored + fully in-range: trust rises above the flat estimate (0.3), toward the
    # emit floor — printing still requires the pixel round-trip to agree in the pipeline
    assert d.confidence > 0.5

    # points outside the measured ranges cut the trust and are called out
    stray = '{"series":[{"label":"a","points":[[0,1],[40,90],[50,99]]}]}'
    d2 = vlm_digitize(Path("x.png"), Fake(stray), cal)
    assert d2.confidence < 0.3 and "outside the calibrated ranges" in d2.note


def test_figure_labels_transcribes_and_trims_loops():
    from pathlib import Path

    from pdf2md.labels import figure_labels

    class Fake:
        def __init__(self, reply):
            self.reply = reply

        def describe(self, *a, **k):
            return self.reply

    fl = figure_labels(Path("x.png"), Fake("m/z\n709.8961(2)\n710.3911(2)\nInten."))
    assert "709.8961(2)" in fl.text and fl.confidence < 1.0  # printed-text OCR, not lossless

    # the OCR model can loop; the guard trims it and the note says so (never a silent drop)
    fl2 = figure_labels(Path("x.png"), Fake("real label\n" + "\n".join(["Intensity"] * 30)))
    assert "real label" in fl2.text and "repetition loop" in fl2.note

    # nothing printed -> None, so the figure stays a plain crop rather than an empty block
    assert figure_labels(Path("x.png"), Fake("")) is None


def test_figure_labels_ocr_picks_upright_and_filters_garbage(tmp_path):
    import numpy as np
    from PIL import Image

    from pdf2md.labels import figure_labels_ocr

    img = Image.new("RGB", (40, 20), "white")  # landscape crop; the upright read is a 90° rotation
    crop = tmp_path / "crop.png"
    img.save(crop)

    class Res:
        def __init__(self, txts):
            self.txts, self.scores = txts, [0.9] * len(txts)
            self.boxes = np.array(
                [[[i * 10, 0], [i * 10 + 8, 0], [i * 10 + 8, 8], [i * 10, 8]] for i in range(len(txts))],
                dtype=float,
            )

    clean, garbage = ["0.0", "0.5", "signal", "time"], ["2'0", "`0-", "口"]

    def reader(arr):  # a wrong (sideways) orientation reads as garbage; upright reads clean
        return Res(clean if arr.shape[0] > arr.shape[1] else garbage)

    fl = figure_labels_ocr(crop, reader)
    assert fl is not None
    assert "signal" in fl.text and "0.5" in fl.text  # kept the legible upright read
    assert "口" not in fl.text                         # punctuation-salad garbage dropped
    assert "90°" in fl.note                           # the rotation was detected and reported

    # a crop that reads as garbage at every orientation yields nothing, so it stays a plain crop
    assert figure_labels_ocr(crop, lambda arr: Res(garbage)) is None


def test_extract_caption_splits_it_out_of_labels():
    from pdf2md.labels import extract_caption

    # a caption OCR broke across lines: the Fig line is just the label, the sentence follows
    text = "0.0\n1.0\nFIG. 2a.\nComparison of u-velocity through geometric center and primary vortex\ncenter."
    cap, remaining = extract_caption(text)
    assert cap == "FIG. 2a. Comparison of u-velocity through geometric center and primary vortex center."
    assert remaining == "0.0\n1.0"          # caption lifted out, tick labels kept

    # a caption already whole on the Fig line stops there, not swallowing the next label
    cap2, rem2 = extract_caption("Figure 1: The result.\nPROFILES THROUGH")
    assert cap2 == "Figure 1: The result." and rem2 == "PROFILES THROUGH"

    # no figure caption present -> nothing promoted, labels untouched
    assert extract_caption("0.0\naxis title\n1.0") == (None, "0.0\naxis title\n1.0")


def test_figure_labels_numeric_consensus_flags_digit_slips():
    from pathlib import Path

    from pdf2md.labels import figure_labels

    class Cycler:
        """Returns a different read per call, so consensus sees independent votes."""

        def __init__(self, replies):
            self.replies = replies
            self.n = 0

        def describe(self, *a, **k):
            r = self.replies[self.n % len(self.replies)]
            self.n += 1
            return r

    # two reads agree on the value, one slips a digit: both are kept (nothing dropped) and
    # the block is flagged low-confidence so a human checks the numbers against the image.
    fl = figure_labels(
        Path("x.png"),
        Cycler(["m/z 494.3080", "m/z 494.3080", "m/z 494.3090"]),
        votes=3,
    )
    assert "494.3080" in fl.text and "494.3090" in fl.text
    assert "disagreed" in fl.note and fl.confidence < 0.6

    # all reads agree -> no flag, base confidence
    fl2 = figure_labels(Path("x.png"), Cycler(["m/z 709.8961"] * 3), votes=3)
    assert "disagreed" not in fl2.note and fl2.confidence == 0.6

    # one vote reads axis ticks the others missed: they join the union and, since nothing
    # conflicts, the block is NOT flagged and keeps base confidence (reading more != a slip).
    fl3 = figure_labels(
        Path("x.png"),
        Cycler(["m/z 709.8961", "m/z 709.8961", "m/z 709.8961\n0.8\n0.6\n0.4"]),
        votes=3,
    )
    assert all(t in fl3.text for t in ("709.8961", "0.8", "0.6", "0.4"))
    assert "disagreed" not in fl3.note and fl3.confidence == 0.6


def test_strip_narration_drops_sentences_keeps_labels():
    from pdf2md.labels import _strip_narration

    text = (
        "m/z\n"
        "709.8961(2)\n"
        "The graph shows the intensity distribution of ions\n"  # narration
        "Relative Intensity\n"
        "a red line tracks the base peak corresponding to peak 495.6135\n"  # hallucinated value
        "[M+2H]2+"
    )
    kept, stripped = _strip_narration(text)
    assert stripped
    assert "709.8961(2)" in kept and "Relative Intensity" in kept and "[M+2H]2+" in kept
    # the describing sentence and the fake peak it carried are both gone
    assert "graph shows" not in kept and "495.6135" not in kept

    # short model meta-talk ends in a period -> dropped; a title/unit without one stays
    text2 = (
        "Scatterplot of dT and A (s^{-1})\n"
        "Let's re-read the image carefully.\n"
        "No text is present in the image.\n"
        "A (s^{-1})"
    )
    kept2, _ = _strip_narration(text2)
    assert "Scatterplot of dT" in kept2 and "A (s^{-1})" in kept2
    assert "re-read" not in kept2 and "No text is present" not in kept2

    # a labels-only read is returned untouched, unflagged
    clean, s2 = _strip_narration("m/z\n709.8961(2)\nRelative Intensity")
    assert not s2 and clean == "m/z\n709.8961(2)\nRelative Intensity"


def test_figure_labels_removes_narration_and_notes_it():
    from pathlib import Path

    from pdf2md.labels import figure_labels

    class Fake:
        def __init__(self, reply):
            self.reply = reply

        def describe(self, *a, **k):
            return self.reply

    fl = figure_labels(
        Path("x.png"),
        Fake("m/z\n709.8961(2)\nThe spectrum displays a series of peaks across the range"),
    )
    assert "709.8961(2)" in fl.text and "spectrum displays" not in fl.text
    assert "describing sentences were removed" in fl.note


def test_figure_labels_textlayer_reads_born_digital_exactly():
    from pdf2md.labels import figure_labels_textlayer
    from pdf2md.schema import BBox

    class FakePC:
        def __init__(self, text):
            self.text = text

        def text_lines(self, bbox):
            return self.text

    bbox = BBox(x0=0, y0=0, x1=10, y1=10)

    # a born-digital figure's exact labels come straight through, tagged as text-layer (not OCR)
    fl = figure_labels_textlayer(FakePC("E (cm-1)\nZ\n25 30 35 40"), bbox)
    assert fl.text == "E (cm-1)\nZ\n25 30 35 40" and fl.confidence == 0.9
    assert "text layer" in fl.note and "not OCR" in fl.note

    # no text layer (a scanned/raster figure) -> None, so the caller falls back to the VLM
    assert figure_labels_textlayer(None, bbox) is None
    # a region with no real text -> None (don't pass off a stray glyph as the labels)
    assert figure_labels_textlayer(FakePC("  \n \n"), bbox) is None


def test_figure_labels_passes_max_tokens():
    from pathlib import Path

    from pdf2md.labels import figure_labels

    seen = {}

    class Spy:
        def describe(self, *a, **k):
            seen["max_tokens"] = k.get("max_tokens")
            return "m/z 709.8961"

    # default: no cap forced, so a reasoning model gets the full vision budget to think
    figure_labels(Path("x.png"), Spy())
    assert seen["max_tokens"] is None

    # a configured cap is threaded through for a looping model
    figure_labels(Path("x.png"), Spy(), max_tokens=1024)
    assert seen["max_tokens"] == 1024


def test_figure_labels_caches_reads(tmp_path):
    from pdf2md.labels import figure_labels

    class Counter:
        def __init__(self):
            self.calls = 0

        def describe(self, *a, **k):
            self.calls += 1
            return "m/z\n709.8961(2)"

        def model_for(self, kind):
            return "m"

    crop = tmp_path / "fig.png"
    crop.write_bytes(b"fake-png-bytes")
    d = Counter()
    cache: dict = {}

    fl = figure_labels(crop, d, votes=2, cache=cache)
    assert "709.8961(2)" in fl.text and d.calls == 2 and len(cache) == 2

    # second run over the same crop bytes serves from cache: no new model calls
    fl2 = figure_labels(crop, d, votes=2, cache=cache)
    assert fl2.text == fl.text and d.calls == 2


def test_render_estimate_draws_the_series():
    from pdf2md.digitize import _render_estimate

    # the round-trip reconstruction: renders the estimate to a plot image (no matplotlib)
    img = _render_estimate([[(0.0, 0.0), (1.0, 1.0), (2.0, 4.0)]])
    assert img.mode == "RGB" and img.size == (320, 320)
    # not blank: the axis box + curve leave black pixels
    assert img.getextrema() != ((255, 255), (255, 255), (255, 255))


def test_pixel_fit_rewards_shape_match_over_mismatch(tmp_path):
    from PIL import Image, ImageDraw

    from pdf2md.digitize import pixel_fit

    # a plot whose only ink is a diagonal running top-left -> bottom-right (image y is down)
    img = Image.new("L", (100, 100), 255)
    ImageDraw.Draw(img).line([(10, 10), (90, 90)], fill=0, width=2)
    crop = tmp_path / "plot.png"
    img.save(crop)

    # a series tracking that diagonal (data y up: y falls as x rises) lands on the ink
    match = pixel_fit(crop, [[(0.0, 10.0), (5.0, 5.0), (10.0, 0.0)]])
    # the opposite diagonal (y rises with x) mostly floats off the drawn ink
    miss = pixel_fit(crop, [[(0.0, 0.0), (5.0, 5.0), (10.0, 10.0)]])
    assert match > 0.5 and miss < match

    # no ink / degenerate input -> 0.0 (a check that can't run fails closed, never inflates)
    blank = tmp_path / "blank.png"
    Image.new("L", (100, 100), 255).save(blank)
    assert pixel_fit(blank, [[(0.0, 0.0), (1.0, 1.0)]]) == 0.0


def test_vector_digitizer_recovers_scatter_markers():
    from pathlib import Path

    import pypdfium2 as pdfium

    from pdf2md.digitize import VectorPathDigitizer
    from pdf2md.schema import BBox

    # scatter (marker forms, no connecting line) of y = x^2/10 for x = 0..10.
    pdf_path = Path(__file__).parent / "fixtures" / "scatter_plot.pdf"
    doc = pdfium.PdfDocument(str(pdf_path))
    w, h = doc[0].get_size()
    doc.close()

    result = VectorPathDigitizer().digitize(pdf_path, 1, BBox(x0=0, y0=0, x1=w, y1=h))
    assert result is not None and result.confidence > 0.95
    (series,) = result.series
    assert len(series) == 11  # one marker per data point
    for (_, got), want in zip(series, [round(v * v / 10, 3) for v in range(11)]):
        assert abs(got - want) < 0.1


def test_svg_crop_exports_vector_region(tmp_path):
    import shutil as _shutil

    import pytest

    from pdf2md.render import svg_crop
    from pdf2md.schema import BBox

    if _shutil.which("pdftocairo") is None:
        pytest.skip("pdftocairo (poppler) not installed")
    from pathlib import Path

    pdf_path = Path(__file__).parent / "fixtures" / "vector_plot.pdf"
    out = tmp_path / "fig.svg"
    assert svg_crop(pdf_path, 1, BBox(x0=0, y0=200, x1=200, y1=0), out)
    body = out.read_text()
    assert body.lstrip().startswith("<?xml") and "<svg" in body
    # the export is the REGION (via a temp cropbox'd page), not the whole 288pt page —
    # pdftocairo's own -x/-W crop flags are silently ignored for SVG output
    import re as _re

    w = float(_re.search(r'width="([\d.]+)pt"', body).group(1))
    assert w < 250

    # a malformed (degenerate) bbox declines instead of exporting a sliver
    assert not svg_crop(pdf_path, 1, BBox(x0=0, y0=1, x1=1, y1=0), tmp_path / "no.svg")

    # an embedded-raster figure would export as an SVG wrapping the bitmap (base64 blob,
    # no text value) — it's declined and the file removed, so the PNG stays the record
    from PIL import Image

    raster_pdf = tmp_path / "raster.pdf"
    Image.new("RGB", (200, 150), "gray").save(raster_pdf)
    out2 = tmp_path / "raster.svg"
    assert not svg_crop(raster_pdf, 1, BBox(x0=0, y0=100, x1=100, y1=0), out2)
    assert not out2.exists()


def test_vector_digitizer_separates_two_scatter_series():
    from pathlib import Path

    import pypdfium2 as pdfium

    from pdf2md.digitize import VectorPathDigitizer
    from pdf2md.schema import BBox

    # two marker styles (o / s, different colors): y = x and y = 8 - x for x = 0..8
    pdf_path = Path(__file__).parent / "fixtures" / "scatter_two_series.pdf"
    doc = pdfium.PdfDocument(str(pdf_path))
    w, h = doc[0].get_size()
    doc.close()

    result = VectorPathDigitizer().digitize(pdf_path, 1, BBox(x0=0, y0=0, x1=w, y1=h))
    assert result is not None and result.kind == "scatter" and result.confidence > 0.95
    assert sorted(len(s) for s in result.series) == [9, 9]  # split by marker style, not merged
    ends = sorted(round(s[-1][1]) for s in result.series)
    assert ends == [0, 8]  # one series rises to 8, the other falls to 0


def test_vector_digitizer_reads_two_panel_figure():
    from pathlib import Path

    import pypdfium2 as pdfium

    from pdf2md.digitize import VectorPathDigitizer
    from pdf2md.schema import BBox

    # two stacked subplots with different y ranges: top y = x (0..10), bottom y = 100-10x
    pdf_path = Path(__file__).parent / "fixtures" / "subplots.pdf"
    doc = pdfium.PdfDocument(str(pdf_path))
    w, h = doc[0].get_size()
    doc.close()

    result = VectorPathDigitizer().digitize(pdf_path, 1, BBox(x0=0, y0=0, x1=w, y1=h))
    assert result is not None and result.confidence > 0.95
    assert result.series_names == ["panel 1 series 1", "panel 2 series 1"]
    (top, bottom) = result.series
    assert abs(top[-1][1] - 10) < 0.1      # panel 1 calibrated to its own 0..10 axis
    assert abs(bottom[0][1] - 100) < 0.5   # panel 2 to its 0..100 axis, not panel 1's


def test_drop_outlier_rescues_a_poisoned_tick_band():
    from pdf2md.digitize import _drop_outlier

    # a rotated axis title OCR-fragments into the y band as '1', wrecking a clean
    # log sequence; leave-one-out finds and drops exactly that tick
    poisoned = [(0.01, 710.0), (0.001, 676.0), (1.0, 658.0), (0.0001, 642.0), (1e-05, 609.0)]
    kept = _drop_outlier(poisoned)
    assert (1.0, 658.0) not in kept and len(kept) == 4

    # a genuinely scattered axis has no single savior tick: left alone (stays flagged)
    bad = [(5.0, 10.0), (1.0, 20.0), (4.0, 30.0), (2.0, 40.0)]
    assert _drop_outlier(bad) == bad


def test_restore_log_signs_flips_descending_powers_of_ten():
    from pdf2md.digitize import restore_log_signs

    # pdfium dropped the superscript minus: 10^-1..10^-3 read as 10, 100, 1000 down the
    # axis — monotonic, so only the powers-of-ten prior can catch it
    ticks = [(1000.0, 10.0), (100.0, 20.0), (10.0, 30.0), (1.0, 40.0)]
    fixed, flipped = restore_log_signs(ticks)
    assert flipped and [v for v, _ in fixed] == [0.001, 0.01, 0.1, 1.0]

    # decimal log ticks and rising sequences are left alone
    assert restore_log_signs([(0.5, 1.0), (0.05, 2.0)])[1] is False
    assert restore_log_signs([(1.0, 1.0), (10.0, 2.0)])[1] is False


def test_vector_digitizer_recovers_bar_chart():
    from pathlib import Path

    import pypdfium2 as pdfium

    from pdf2md.digitize import VectorPathDigitizer
    from pdf2md.schema import BBox

    # bars of height [3, 7, 5, 9, 4] at x = 1..5 on a common baseline
    pdf_path = Path(__file__).parent / "fixtures" / "bar_plot.pdf"
    doc = pdfium.PdfDocument(str(pdf_path))
    w, h = doc[0].get_size()
    doc.close()

    result = VectorPathDigitizer().digitize(pdf_path, 1, BBox(x0=0, y0=0, x1=w, y1=h))
    assert result is not None and result.kind == "bar" and result.confidence > 0.95
    (series,) = result.series
    for (x, got), (wx, want) in zip(series, [(1, 3), (2, 7), (3, 5), (4, 9), (5, 4)]):
        assert abs(x - wx) < 0.05 and abs(got - want) < 0.05


def test_digitize_reads_superscript_and_log_axis():
    from pdf2md.digitize import fit_axis, _token_value

    # a log tick '10' + a smaller, raised '3' is 10^3, not the literal 103
    chars = [("1", 14, 30, 7.0), ("0", 20, 30, 7.0), ("3", 26, 33, 5.0)]
    assert _token_value(chars) == 1000.0
    # a plain decimal is unchanged (period sits low, not raised -> stays in the base)
    assert _token_value([("0", 14, 30, 7.0), (".", 18, 28, 1.5), ("2", 22, 30, 7.0)]) == 0.2

    # geometric tick values on evenly spaced positions -> a log axis, fit and flagged as such
    ticks = [(1.0, 30), (10.0, 76), (100.0, 121), (1000.0, 167)]
    _, r2, kind = fit_axis(ticks)
    assert kind == "log" and r2 > 0.999


def test_digitize_restores_dropped_negative_signs():
    from pdf2md.digitize import restore_signs

    # the text layer drops matplotlib's minus glyph, so ticks -4..4 parse as 4,2,0,2,4
    # by ascending page position (bottom-left origin: y up); monotonicity restores signs.
    ticks = [(4.0, 40), (2.0, 73), (0.0, 107), (2.0, 140), (4.0, 173)]
    restored, flipped = restore_signs(ticks)
    assert [v for v, _ in restored] == [-4, -2, 0, 2, 4] and flipped  # flip reported for the haircut
    # a genuinely monotonic (all-positive) axis is left untouched, no flip.
    pos = [(0.0, 10), (5.0, 50), (10.0, 90)]
    assert restore_signs(pos) == (pos, False)


def test_consensus_agrees_on_numbers_ignores_wording():
    from pdf2md.consensus import consensus_pick, numeric_tokens

    assert numeric_tokens("D was 1,234.5 at pH 3 (10^-2)") == ["1234.5", "3", "10", "-2"]

    # Same numbers, different prose wording -> agreement, not a conflict.
    chosen, agreed = consensus_pick(["The ratio was 0.38 at 25 C.", "Ratio: 0.38 (25 C)."])
    assert agreed and chosen == "The ratio was 0.38 at 25 C."

    # A flipped digit -> disagreement; the modal reading (2 of 3) is kept.
    chosen, agreed = consensus_pick(["value 0.38", "value 0.88", "value 0.38"])
    assert not agreed and chosen == "value 0.38"

    # A single read (votes=1, the default) never claims a conflict.
    assert consensus_pick(["only 5.0"]) == ("only 5.0", True)


def test_merge_reads_unions_labels_and_flags_only_slips():
    from pdf2md.consensus import merge_reads

    # A value only one vote saw joins the union; reading more is not a conflict.
    text, conflict = merge_reads(["m/z 709.8961", "m/z 709.8961\n0.8\n0.6"])
    assert not conflict
    assert text.splitlines() == ["m/z 709.8961", "0.8", "0.6"]  # deduped label + extra ticks

    # Same label read with a slipped digit: both kept (nothing dropped), flagged a conflict.
    text, conflict = merge_reads(["m/z 494.3080", "m/z 494.3090"])
    assert conflict and "494.3080" in text and "494.3090" in text

    # Same wording, same numbers across votes -> one copy, no conflict.
    text, conflict = merge_reads(["Relative Intensity", "Relative Intensity"])
    assert not conflict and text == "Relative Intensity"

    # A case-variant re-read is the same label (one copy), and a stray punctuation-only
    # rule line carries no label so it is dropped from the union.
    text, conflict = merge_reads(["Geometric Center", "GEOMETRIC CENTER\n---"])
    assert not conflict and text == "Geometric Center"

    # the same axis title in three LaTeX-delimiter forms collapses to one line, not three
    text, conflict = merge_reads(["$P(r)$", "$$P(r)$$\nP(r)"])
    assert not conflict and text == "$P(r)$"

    # A single read never claims a conflict.
    assert merge_reads(["m/z 709.8961"]) == ("m/z 709.8961", False)


def test_table_consensus_preserves_disagreements_and_missing_cells():
    from pdf2md.consensus import table_cell_consensus

    cells = table_cell_consensus({
        "mineru": [["R", "0.0209", "0.2524"], ["0.1", "1.0"]],
        "paddle": [["R", "0.0709", "0.2524"], ["0.1", "1.0", "2.0"]],
        "glyph": [["R", "0.0209", "0.2524"], ["0.1", "1.0"]],
    })
    by_coordinate = {(cell["row"], cell["column"]): cell for cell in cells}

    assert by_coordinate[0, 0]["status"] == "agree"
    assert by_coordinate[0, 1] == {
        "row": 0,
        "column": 1,
        "status": "majority",
        "selected": "0.0209",
        "readings": {"mineru": "0.0209", "paddle": "0.0709", "glyph": "0.0209"},
    }
    assert by_coordinate[1, 2]["status"] == "single_read"

    tied = table_cell_consensus({"a": [["0.0250"]], "b": [["0.0750"]]})
    assert tied[0]["status"] == "disagree" and tied[0]["selected"] is None


def test_norm_title_dedup_and_initial_guard():
    from pdf2md.emit import _norm_title

    # bookmark title and the part-prefixed page heading normalise equal (dedup works)
    assert _norm_title("IV Issues of convergence") == _norm_title("Part IV: Issues of convergence")
    assert _norm_title("I Overview of GRASP") == "overview of grasp"
    # an initial ("C.") is NOT read as a section numeral (the period blocks the bare form)
    assert _norm_title("C. elegans data") == "c elegans data"
    assert _norm_title("Introduction") == "introduction"  # leading "I" not stripped from a word


def test_table_strips_caption_prefix():
    t = TableData("#/tables/0", 1, None, gfm="Table 1: x\n\n| a |\n|---|\n| 1 |")
    assert render_table(t).startswith("| a |")


def test_table_html_fallback_for_spanning_cells():
    t = TableData("#/tables/0", 1, None, gfm="ignored", html="<table></table>", has_spanning_cells=True)
    assert render_table(t) == "<table></table>"


def test_repeated_table_panels_split_and_continue_across_fragments():
    from pdf2md.tables import split_repeated_panels

    first = [
        ["WAVE FUNCTIONS FOR ATOMIC NUMBER 29.", "", "", "",
         "WAVE FUNCTIONS FOR ATOMIC NUMBER 30.", "", "", ""],
        ["RADIUS", "1S", "2S", "2P", "RADIUS", "1S", "2S", "2P"],
        ["0.0001", "0.0306", "0.0094", ".", "0.0001", "0.0322", "0.0100", "."],
    ]
    continuation = [[
        "0.180", "0.3438", "-2.1328", "2.1138",
        "0.180", "0.3040", "-2.1953", "2.1343",
    ]]

    panels, layout = split_repeated_panels(first)
    continued, same_layout = split_repeated_panels(continuation, layout)

    assert layout is not None and same_layout == layout
    assert panels == [
        {
            "title": "WAVE FUNCTIONS FOR ATOMIC NUMBER 29.",
            "columns": ["RADIUS", "1S", "2S", "2P"],
            "rows": [["0.0001", "0.0306", "0.0094", "."]],
            "source_rows": [2],
            "refused_rows": [],
            "title_cells": ["WAVE FUNCTIONS FOR ATOMIC NUMBER 29.", "", "", ""],
            "source_start": 0,
            "source_data_start": 2,
        },
        {
            "title": "WAVE FUNCTIONS FOR ATOMIC NUMBER 30.",
            "columns": ["RADIUS", "1S", "2S", "2P"],
            "rows": [["0.0001", "0.0322", "0.0100", "."]],
            "source_rows": [2],
            "refused_rows": [],
            "title_cells": ["WAVE FUNCTIONS FOR ATOMIC NUMBER 30.", "", "", ""],
            "source_start": 4,
            "source_data_start": 2,
        },
    ]
    assert continued[0]["rows"] == [["0.180", "0.3438", "-2.1328", "2.1138"]]
    assert continued[1]["rows"] == [["0.180", "0.3040", "-2.1953", "2.1343"]]


def test_repeated_table_panels_allow_unequal_widths():
    from pdf2md.tables import split_repeated_panels

    rows = [
        ["ATOM 2", "", "ATOM 3", "", "", "ATOM 4", "", ""],
        ["RADIUS", "1S", "RADIUS", "1S", "2S", "RADIUS", "1S", "2S"],
        ["0.1", "1.0", "0.1", "2.0", "3.0", "0.1", "4.0", "5.0"],
    ]

    panels, layout = split_repeated_panels(rows)

    assert layout is not None and layout.widths == (2, 3, 3)
    assert [panel["columns"] for panel in panels] == [
        ["RADIUS", "1S"],
        ["RADIUS", "1S", "2S"],
        ["RADIUS", "1S", "2S"],
    ]
    assert panels[0]["rows"] == [["0.1", "1.0"]]


def test_repeated_table_panels_preserve_independent_source_rows():
    from pdf2md.tables import split_repeated_panels

    rows = [
        ["RADIUS", "1S", "RADIUS", "1S"],
        ["0.1", "1.0", "0.1", "2.0"],
        ["0.2", "1.1", "", ""],
        ["", "", "0.2", "2.1"],
    ]

    panels, _ = split_repeated_panels(rows)

    assert panels[0]["rows"] == [["0.1", "1.0"], ["0.2", "1.1"]]
    assert panels[0]["source_rows"] == [1, 2]
    assert panels[1]["rows"] == [["0.1", "2.0"], ["0.2", "2.1"]]
    assert panels[1]["source_rows"] == [1, 3]


def test_repeated_table_panels_refuse_shifted_and_ambiguous_rows():
    from pdf2md.tables import split_repeated_panels

    rows = [
        ["RADIUS", "1S", "2S", "2P", "RADIUS", "1S", "2S", "2P",
         "RADIUS", "1S", "2S", "2P"],
        ["2.600", "0.0002", "-0.1967", "0.2836",
         "2.600", "0.0001", "-0.1344", "0.2233",
         "2.600", "-0.0906", "0.1754", ""],
        ["3.000", "-", "-0.1190", "0.2010",
         "3.000", "-0.0750", "0.1505", "3.000",
         "3.000", "-0.0468", "0.1128", ""],
        ["3.500", "-", "-0.0622", "0.1287",
         "3.500", "-0.0350", "0.0906", "3.500",
         "-0.0350", "-0.0199", "0.0641", ""],
    ]

    panels, _ = split_repeated_panels(rows)

    assert panels[0]["rows"] == [
        ["2.600", "0.0002", "-0.1967", "0.2836"],
        ["3.000", "-", "-0.1190", "0.2010"],
        ["3.500", "-", "-0.0622", "0.1287"],
    ]
    assert panels[0]["refused_rows"] == []
    assert panels[1]["rows"] == [["2.600", "0.0001", "-0.1344", "0.2233"]]
    assert panels[1]["refused_rows"] == [
        {
            "source_row": 2,
            "reason": "ambiguous_shifted_panel_boundary",
            "cells": ["3.000", "-0.0750", "0.1505", "3.000"],
        },
        {
            "source_row": 3,
            "reason": "ambiguous_shifted_panel_boundary",
            "cells": ["3.500", "-0.0350", "0.0906", "3.500"],
        },
    ]
    assert panels[2]["rows"] == []
    assert panels[2]["refused_rows"] == [
        {
            "source_row": 1,
            "reason": "ambiguous_trailing_blank",
            "cells": ["2.600", "-0.0906", "0.1754", ""],
        },
        {
            "source_row": 2,
            "reason": "ambiguous_shifted_panel_boundary",
            "cells": ["3.000", "-0.0468", "0.1128", ""],
        },
        {
            "source_row": 3,
            "reason": "ambiguous_shifted_panel_boundary",
            "cells": ["-0.0350", "-0.0199", "0.0641", ""],
        },
    ]


def test_repeated_table_panels_detect_shift_in_two_panel_row():
    from pdf2md.tables import split_repeated_panels

    rows = [
        ["RADIUS", "1S", "2S", "RADIUS", "1S", "2S"],
        ["1.000", "0.5", "1.000", "0.6", "0.7", ""],
        ["2.000", "0.4", "2.000", "0.8", "0.9", ""],
    ]

    panels, _ = split_repeated_panels(rows)

    assert [panel["rows"] for panel in panels] == [[], []]
    assert [panel["refused_rows"][0]["reason"] for panel in panels] == [
        "ambiguous_shifted_panel_boundary",
        "ambiguous_shifted_panel_boundary",
    ]


def test_build_html_spans_and_headers():
    from pdf2md.tables import GridCell, build_html

    cells = [
        GridCell("H", 0, 0, col_span=2, header=True),
        GridCell("a", 1, 0),
        GridCell("b", 1, 1),
    ]
    html = build_html(cells, 2, 2)
    assert '<th colspan="2">H</th>' in html
    assert "<tr><td>a</td><td>b</td></tr>" in html


def test_build_gfm_derives_header_from_flags():
    from pdf2md.tables import GridCell, build_gfm

    cells = [
        GridCell("A", 0, 0, header=True),
        GridCell("B", 0, 1, header=True),
        GridCell("1", 1, 0),
        GridCell("2", 1, 1),
    ]
    assert build_gfm(cells, 2, 2).splitlines() == ["| A | B |", "|---|---|", "| 1 | 2 |"]


def test_content_hash(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello")
    assert content_hash(p) == hashlib.sha256(b"hello").hexdigest()


def test_document_slug_is_readable_and_safe():
    assert document_slug(Path("Supporting_information (final).pdf")) == (
        "supporting-information-final"
    )
    assert document_slug(Path(".pdf")) == "pdf"


def test_doc_dir_combines_source_name_with_short_identity(tmp_path):
    source = tmp_path / "My Paper.pdf"
    source.write_bytes(b"paper")
    doc_id = content_hash(source)

    assert doc_dir(doc_id, source, root=tmp_path / "out") == (
        tmp_path / "out" / f"my-paper-{doc_id[:8]}"
    )


def test_doc_dir_reuses_legacy_hash_only_directory(tmp_path):
    source = tmp_path / "My Paper.pdf"
    source.write_bytes(b"paper")
    doc_id = content_hash(source)
    legacy = tmp_path / "out" / doc_id[:16]
    legacy.mkdir(parents=True)
    (legacy / "source.pdf").write_bytes(source.read_bytes())

    assert doc_dir(doc_id, source, root=tmp_path / "out") == legacy


def test_doc_dir_reuses_content_after_source_rename(tmp_path):
    source = tmp_path / "Renamed Paper.pdf"
    source.write_bytes(b"paper")
    doc_id = content_hash(source)
    existing = tmp_path / "out" / f"original-name-{doc_id[:8]}"
    existing.mkdir(parents=True)
    (existing / "source.pdf").write_bytes(source.read_bytes())

    assert doc_dir(doc_id, source, root=tmp_path / "out") == existing


def test_doc_dir_extends_hash_when_readable_name_collides(tmp_path):
    source = tmp_path / "Paper.pdf"
    source.write_bytes(b"paper")
    doc_id = content_hash(source)
    collision = tmp_path / "out" / f"paper-{doc_id[:8]}"
    collision.mkdir(parents=True)
    (collision / "source.pdf").write_bytes(b"different paper")

    assert doc_dir(doc_id, source, root=tmp_path / "out") == (
        tmp_path / "out" / f"paper-{doc_id[:12]}"
    )


def _complete_version(dd, n):
    (dd / f"v{n}").mkdir(parents=True)
    (dd / f"v{n}" / "provenance.json").write_text("{}")


def test_versioning(tmp_path):
    dd = tmp_path / "doc"
    assert latest_version(dd) is None
    assert next_version(dd) == 1
    _complete_version(dd, 1)
    _complete_version(dd, 2)
    assert latest_version(dd) == 2
    assert next_version(dd) == 3


def test_incomplete_version_ignored(tmp_path):
    # A crashed run leaves a version dir with crops but no provenance.json (written
    # last). It must not count as cached output, or it blocks every later run.
    dd = tmp_path / "doc"
    (dd / "v1" / "assets").mkdir(parents=True)  # crops written, crash before emit/provenance
    assert latest_version(dd) is None
    assert next_version(dd) == 1                # the next run reuses v1, not v2


def test_deduplicate_assets_links_exact_matches_and_survives_prune(tmp_path, monkeypatch):
    from pdf2md.cache import deduplicate_assets, prune

    monkeypatch.setenv("PDF2MD_OUT", str(tmp_path))
    source_bytes = b"source"
    dd = tmp_path / f"paper-{hashlib.sha256(source_bytes).hexdigest()[:8]}"
    dd.mkdir()
    (dd / "source.pdf").write_bytes(source_bytes)
    old_assets = dd / "v1" / "assets"
    new_assets = dd / "v2" / "assets"
    old_assets.mkdir(parents=True)
    new_assets.mkdir(parents=True)
    (dd / "v1" / "provenance.json").write_text("{}")
    (old_assets / "figure.png").write_bytes(b"same pixels")
    (old_assets / "page_001.png").write_bytes(b"same page")
    (new_assets / "renamed.png").write_bytes(b"same pixels")
    (new_assets / "page_001.png").write_bytes(b"same page")
    (new_assets / "changed.png").write_bytes(b"different")

    linked, saved = deduplicate_assets(dd / "v2")

    assert linked == 2
    assert saved == len(b"same pixels") + len(b"same page")
    assert (new_assets / "renamed.png").samefile(old_assets / "figure.png")
    assert (new_assets / "page_001.png").samefile(old_assets / "page_001.png")
    assert not (new_assets / "changed.png").samefile(old_assets / "figure.png")

    (dd / "v2" / "provenance.json").write_text("{}")
    prune(keep=1)
    assert (new_assets / "renamed.png").read_bytes() == b"same pixels"
    assert (new_assets / "page_001.png").read_bytes() == b"same page"


def test_prune_keeps_newest(tmp_path, monkeypatch):
    from pdf2md.cache import prune

    monkeypatch.setenv("PDF2MD_OUT", str(tmp_path))
    source_bytes = b"source"
    dd = tmp_path / f"paper-{hashlib.sha256(source_bytes).hexdigest()[:8]}"
    dd.mkdir()
    (dd / "source.pdf").write_bytes(source_bytes)
    for v in (1, 2, 3):
        (dd / f"v{v}").mkdir(parents=True)

    removed = prune(keep=1)

    assert {p.name for p in removed} == {"v1", "v2"}
    assert (dd / "v3").exists()
    assert not (dd / "v1").exists() and not (dd / "v2").exists()


def test_prune_dry_run_removes_nothing(tmp_path, monkeypatch):
    from pdf2md.cache import prune

    monkeypatch.setenv("PDF2MD_OUT", str(tmp_path))
    source_bytes = b"source"
    dd = tmp_path / f"paper-{hashlib.sha256(source_bytes).hexdigest()[:8]}"
    dd.mkdir()
    (dd / "source.pdf").write_bytes(source_bytes)
    for v in (1, 2):
        (dd / f"v{v}").mkdir(parents=True)

    removed = prune(keep=1, dry_run=True)

    assert len(removed) == 1
    assert (dd / "v1").exists()  # untouched


def test_prune_ignores_unrelated_versioned_directories(tmp_path, monkeypatch):
    from pdf2md.cache import prune

    monkeypatch.setenv("PDF2MD_OUT", str(tmp_path))
    source_bytes = b"source"
    document = tmp_path / f"paper-{hashlib.sha256(source_bytes).hexdigest()[:8]}"
    document.mkdir()
    (document / "source.pdf").write_bytes(source_bytes)
    unrelated = tmp_path / "experiment"
    for root in (document, unrelated):
        for version in (1, 2):
            (root / f"v{version}").mkdir(parents=True)

    removed = prune(keep=1)

    assert removed == [document / "v1"]
    assert (unrelated / "v1").is_dir()
    assert (unrelated / "v2").is_dir()


def test_coverage_report_separates_accounting_from_completeness():
    blocks = [
        Block("a", BlockType.PARAGRAPH, "x", 1, coverage_status=CoverageStatus.EMITTED),
        Block("b", BlockType.FIGURE, "", 1, coverage_status=CoverageStatus.CROPPED),
        Block("c", BlockType.EQUATION, "", 1, coverage_status=CoverageStatus.FLAGGED),
        Block("d", BlockType.PARAGRAPH, "", 1, coverage_status=CoverageStatus.DROPPED),
    ]
    report = build_report("doc", blocks, [])
    assert (report.total_blocks, report.emitted, report.cropped, report.flagged, report.dropped) == (4, 1, 1, 1, 1)
    assert report.accounted_for is True
    assert report.complete is False
    assert report.needs_review is True
    assert report.lossless is True  # compatibility alias for format 0.6 callers


def test_unglyph_maps_greek_names():
    from pdf2md.normalize import unglyph

    assert unglyph("/Delta1f H") == "Δf H"
    assert unglyph("( 2 /Pi1 )") == "( 2 Π )"
    assert unglyph("E _ { /Sigma1 }") == "E _ { Σ }"


def test_unglyph_leaves_non_glyphs_alone():
    from pdf2md.normalize import unglyph

    assert unglyph("assets/pictures_0.png") == "assets/pictures_0.png"  # pi-prefix, no match
    assert unglyph("</td><th>/Si</th>") == "</td><th>/Si</th>"  # html + silicon untouched
    assert unglyph("plain text") == "plain text"


def test_strip_orphan_combining():
    from pdf2md.normalize import strip_orphan_combining

    assert strip_orphan_combining("̸") == ""          # lone solidus overlay -> nothing
    assert strip_orphan_combining("a ̸b") == "a b"     # orphan after space dropped
    assert strip_orphan_combining("≠") == "≠"   # real base+mark (≠) kept
    assert strip_orphan_combining("plain") == "plain"


def test_religature():
    from pdf2md.normalize import religature, vocabulary

    vocab = vocabulary("the diffuse difference effects of the first cutoff value and off the field")
    # mid-word splits rejoin to the whole word the document reports.
    assert religature("di ff use", vocab) == "diffuse"
    assert religature("di ff erence e ff ects", vocab) == "difference effects"
    # a real word boundary is never fused: the join must match a known word.
    assert religature("cuto ff value", vocab) == "cutoff value"   # join left only
    assert religature("the fi rst", vocab) == "the first"          # join right only
    assert religature("off the", vocab) == "off the"               # already whole, untouched
    # no confirming word in the vocabulary -> left split rather than guess.
    assert religature("x ff y", vocabulary("unrelated text")) == "x ff y"
    # a ligature codepoint in the reference still confirms the ASCII join.
    assert religature("di ff use", vocabulary("a diﬀuse cloud")) == "diffuse"


def test_rejoin_split_word():
    from pdf2md.normalize import rejoin_split_word, vocabulary

    vocab = vocabulary("Lowdin and Mulliken charge data set of the linear part")
    # diacritic-dropped word reassembles: 'Lo' is a stem fragment, not a word.
    assert rejoin_split_word("Lo wdin charge", vocab) == "Lowdin charge"
    # the dropped diacritic often leaves a double space — tolerate it.
    assert rejoin_split_word("Lo  wdin charge", vocab) == "Lowdin charge"
    # even when the broken tail leaked into the vocabulary, the stem guard fires.
    assert rejoin_split_word("Lo wdin", vocabulary("Lowdin wdin elsewhere")) == "Lowdin"
    # legitimate pairs whose LEFT piece is a real word are never fused.
    assert rejoin_split_word("data set", vocab) == "data set"
    assert rejoin_split_word("of the value", vocab) == "of the value"
    assert rejoin_split_word("non linear part", vocab) == "non linear part"
    # no confirming joined word in the vocabulary -> leave it split.
    assert rejoin_split_word("Lo wdin", vocabulary("unrelated")) == "Lo wdin"


def test_assess_equation():
    from pdf2md.confidence import assess_equation

    # Garbled LaTeX (AQCC->AQC/CC, pVTZ->pVTEZ) vs a clean text layer: low score,
    # and the clean text layer is handed back as the reading hint.
    conf, reading = assess_equation(
        r"E ( \text {MR-AQC/CC/cc-pVTEZ) - E ( \text {CASPT} 2 / \text {cc-pVTEZ} ) \quad ( 4 )",
        "E(MR-AQCC/cc-pVTZ) − E(CASPT2/cc-pVTZ) (4)")
    assert conf < 0.85 and reading == "E(MR-AQCC/cc-pVTZ) − E(CASPT2/cc-pVTZ) (4)"

    # Docling spaces out every glyph; once rejoined a faithful LaTeX scores 1.0.
    assert assess_equation(
        r"E ( M R - c c C A ) & = E _ { 0 } ( M R - c c C A )",
        "E(MR-ccCA) = E0(MR-ccCA)") == (1.0, None)

    # \exp / \max produce visible text; stripping them must not fake a mismatch on a
    # correct equation (this is what wrongly recovered Eq 2 and flattened its frac).
    assert assess_equation(
        r"E ( l _ { \max } ) = E _ { C B S } + \frac { D } { ( l _ { \max } + 1 / 2 ) ^ { 4 } }",
        "E(lmax) = ECBS + D (lmax + 1/2)4") == (1.0, None)

    # Too few alphanumeric tokens to judge (symbol-heavy orbital config).
    assert assess_equation(r"[ \text {Core} ] 4 \sigma", "[Core]4σ") is None

    # The page prints an equation number and the engine omits it. That is page
    # furniture, not content, so it leaves both sides rather than scoring as a
    # disagreement -- 57 of 108 equation regions measured here end in one.
    assert assess_equation(
        r"V _ { 0 } \subset V _ { 1 } \subset V _ { 2 } \subset \cdots .",
        "V0 ⊂ V1 ⊂ V2 ⊂ ⋯. (13)") == (1.0, None)

    # And when the engine keeps it, it still leaves both sides -- otherwise the
    # fix would only move the mismatch to the other direction.
    assert assess_equation(
        r"V _ { 0 } \subset V _ { 1 } \subset V _ { 2 } \quad ( 1 3 )",
        "V0 ⊂ V1 ⊂ V2 (13)") == (1.0, None)


def test_a_letter_substituting_font_is_not_a_fit_reference():
    from pdf2md.confidence import is_clean

    # Wiley draws `(14)` as `ð14Þ` and a square root as a run of `ffi`
    # ligatures. Every character is an ordinary letter, so the reading passes
    # each per-character test and is still nonsense -- an equation scored
    # against it reads as a suspect extraction when the LaTeX was right and
    # only the reference was broken. 11 of the 19 suspect equations whose layer
    # was called fit carried one of these.
    assert is_clean("FC = SC\u03b5: \u00f014\u00de") is False
    assert is_clean("R2 AB + \u03b7 \u22122 ffiffiffiffiffiffiffi q") is False

    # A clean reading, and one with a genuine parenthesised number, still pass.
    assert is_clean("FC = SC\u03b5 (14)") is True
    assert is_clean("the coefficient office hours") is True


def test_latex_tokens_do_not_weld_across_structure():
    from pdf2md.confidence import _latex_tokens

    # A command is a token boundary. Deleting it welded a sum's limit onto the
    # next symbol (`bendsUBEND`) and a fraction onto what followed (`12kBEND`),
    # which the text layer can never match because the page sets them apart.
    assert _latex_tokens(r"\sum _ { \text {bends} } U ^ { B E N D }") == {"bends", "UBEND"}
    # The fraction's parts no longer weld onto what follows; the script still
    # attaches to its own base, which is how the layer spells it.
    assert _latex_tokens(r"\frac { 1 } { 2 } k ^ { B E N D }") == {"kBEND"}

    # An environment and its column spec were never visible text.
    assert _latex_tokens(r"\begin{array}{rlr} P _ { n l } \end{array}") == {"Pnl"}

    # Still rejoins Docling's per-glyph spacing, and still attaches a script to
    # an alphanumeric base the way the layer draws it.
    assert _latex_tokens(r"E _ { 0 } ( M R - c c C A )") == {"E0", "MR", "ccCA"}

    # A genuine misread stays a mismatch -- the fix must not launder one away.
    assert _latex_tokens(r"U ^ { i o t }") == {"Uiot"}

    # A column spec that never closes: Docling ran away inside one for 4075
    # characters, and the spec reached the token set as a single 1000-character
    # `cccc...`, counted as content the text layer was missing. Nothing after an
    # unterminated environment spec is visible text.
    assert _latex_tokens(r"\begin{array} { c c c" + " c" * 40) == set()


def test_unsplit_numbers_protects_values():
    from pdf2md.scripts import apply_scripts

    # A digit raised inside a number is a misdetection: 191.4 must stay 191.4.
    scored = [("1", "sup"), ("9", None), ("1", None), (".", None), ("4", None)]
    assert apply_scripts("191.4", scored) == "191.4"
    # ...but a real trailing citation/exponent survives (191.4⁶⁹).
    scored = [(c, None) for c in "191.4"] + [("6", "sup"), ("9", "sup")]
    assert apply_scripts("191.469", scored) == "191.4<sup>69</sup>"
    # A left-superscript multiplicity (²A) is kept — the digit precedes a letter.
    assert apply_scripts("2A1", [("2", "sup"), ("A", None), ("1", "sub")]) == "<sup>2</sup>A<sub>1</sub>"


def test_metadata_heuristic(monkeypatch):
    import pdf2md.metadata as m

    monkeypatch.setattr(m, "_embedded", lambda _p: {})
    blocks = [
        Block("#/texts/0", BlockType.HEADING, "My Great Paper", 1),
        Block("#/texts/1", BlockType.PARAGRAPH, "doi:10.1234/abc published in 2021", 1),
    ]
    meta = m.extract_metadata("ignored.pdf", blocks)
    assert meta["title"] == "My Great Paper"
    assert meta["doi"] == "10.1234/abc"
    assert meta["year"] == "2021"


def test_eq_crops_selects_low_confidence_and_untranscribed():
    from pdf2md.pipeline import _eq_crops
    from pdf2md.schema import BBox, Block, BlockType

    bb = BBox(0, 10, 5, 0)
    good = Block(id="#/e1", type=BlockType.EQUATION, text="E = mc^2", page=1, confidence=1.0, bbox=bb)
    low = Block(id="#/e2", type=BlockType.EQUATION, text="garbled", page=1, confidence=0.0, bbox=bb)
    empty = Block(id="#/e3", type=BlockType.EQUATION, text="  ", page=1, bbox=bb)  # --no-formula: no LaTeX
    nobbox = Block(id="#/e4", type=BlockType.EQUATION, text="", page=1, bbox=None)
    picked = {b.id for b in _eq_crops([good, low, empty, nobbox])}
    assert picked == {"#/e2", "#/e3"}  # low-confidence + untranscribed; verified and bbox-less skip


def test_resegment_words_splits_runons_keeps_tokens_and_real_words():
    from pdf2md.normalize import resegment_words

    # RapidOCR ran words together in a scanned line -> re-split from English frequencies
    assert resegment_words("Lookunderthecab at the topofthehill") == "Look under the cab at the top of the hill"
    # numbers and stat tokens are left intact (only alphabetic runs are touched)
    assert resegment_words("swim+8 fly-16 power+16") == "swim+8 fly-16 power+16"
    # a correctly-spaced line is a no-op; proper nouns and real words stay whole
    assert resegment_words("Sonic runs the configuration") == "Sonic runs the configuration"


def test_space_after_punct_only_before_letters():
    from pdf2md.normalize import space_after_punct

    assert space_after_punct("ramp,toward the screen") == "ramp, toward the screen"
    assert space_after_punct("crates ahead,on your right") == "crates ahead, on your right"
    assert space_after_punct("3,000 and 3,14") == "3,000 and 3,14"  # digit after comma -> untouched
    # sentence stop between lower and upper gets a space; acronyms and versions are protected
    assert space_after_punct("point marker.The dash panels") == "point marker. The dash panels"
    assert space_after_punct("N.A.C.A. Technical Note") == "N.A.C.A. Technical Note"
    assert space_after_punct("run v2.0 at 3.5x") == "run v2.0 at 3.5x"


def test_resegment_ocr_prose_only_touches_ocr_blocks():
    from pdf2md.enrich import resegment_ocr_prose
    from pdf2md.schema import Block, BlockType

    ocr = Block(id="#/t1", type=BlockType.PARAGRAPH, text="Lookunderthecab,then whistle", page=1,
                extra={"ocr": True})
    born = Block(id="#/t2", type=BlockType.PARAGRAPH, text="Lookunderthecab,then whistle", page=1)
    resegment_ocr_prose([ocr, born])
    assert ocr.text == "Look under the cab, then whistle"  # OCR'd scan -> punct spaced + words split
    assert born.text == "Lookunderthecab,then whistle"     # born-digital -> untouched

    # word_split off (non-English scan): comma spacing still applies, words are left as-is
    de = Block(id="#/t3", type=BlockType.PARAGRAPH, text="Wahrheit,und Wille", page=1, extra={"ocr": True})
    resegment_ocr_prose([de], word_split=False)
    assert de.text == "Wahrheit, und Wille"


def test_glyph_index_force_ocr_reports_no_text_layer():
    from pathlib import Path

    from pdf2md.enrich import GlyphIndex

    # A born-digital plot has a real text layer; force_ocr distrusts it and reports every
    # page as having none, so the whole doc is treated as a scan (engine re-OCR stands).
    pdf = Path(__file__).parent / "fixtures" / "vector_plot.pdf"
    normal, forced = GlyphIndex(pdf), GlyphIndex(pdf, force_ocr=True)
    try:
        assert normal.page_chars(1) is not None
        assert forced.page_chars(1) is None
    finally:
        normal.close()
        forced.close()


def test_apply_page_transcripts_replaces_page_prose_keeps_figures():
    from pdf2md.scan_ocr import _apply_page_transcripts
    from pdf2md.schema import Block, BlockType

    blocks = [
        Block("#/eq1", BlockType.EQUATION, "E=mc^2", 1, extra={"ocr": True}),
        Block("#/f1", BlockType.FIGURE, "", 1),
        Block("#/tab1", BlockType.TABLE, "", 1, extra={"ocr": True}),
        Block("#/p3", BlockType.PARAGRAPH, "born-digital page", 2),  # page 2 not a target
        Block("#/f4", BlockType.FIGURE, "", 3),  # page 3 target, only a figure -> no host block
    ]
    out = _apply_page_transcripts(blocks, {1, 3}, {1: "# Heading\n\nfull page text"})
    # page 1: one transcription block + its figure; page 2: untouched; page 3 (target, no
    # transcription) gets a visible marker + its figure.
    assert [b.id for b in out] == ["#/page/1", "#/f1", "#/p3", "#/page/3", "#/f4"]
    p1 = next(b for b in out if b.id == "#/page/1")
    assert p1.text == "# Heading\n\nfull page text" and p1.extra["text_source"] == "vlm-page"
    p3 = next(b for b in out if b.id == "#/page/3")
    assert "not transcribed" in p3.text and p3.extra["text_source"] == "vlm-page-failed"
    assert next(b for b in out if b.id == "#/p3").text == "born-digital page"


def test_apply_page_transcripts_marks_truncated_pages():
    from pdf2md.scan_ocr import _apply_page_transcripts
    from pdf2md.schema import Block, BlockType

    blocks = [Block("#/p1", BlockType.PARAGRAPH, "docling text", 1)]
    out = _apply_page_transcripts(
        blocks, {1, 2}, {1: "capped page", 2: "looped page"},
        cap_truncated={1}, loop_truncated={2},
    )
    p1 = next(b for b in out if b.id == "#/page/1")
    p2 = next(b for b in out if b.id == "#/page/2")
    assert p1.extra.get("ocr_cap_truncated") and "ocr_loop_truncated" not in p1.extra
    assert p2.extra.get("ocr_loop_truncated") and "ocr_cap_truncated" not in p2.extra


def test_emit_flags_cap_truncated_page_visible_and_counted():
    from pdf2md.emit import _Ctx, _render_blocks
    from pdf2md.schema import Block, BlockType, CoverageStatus

    b = Block("#/page/5", BlockType.PARAGRAPH, "transcribed prose", 5,
              extra={"ocr": True, "text_source": "vlm-page", "ocr_cap_truncated": True})
    ctx = _Ctx({}, {}, {})
    body = _render_blocks([b], ctx)
    assert "hit the output token cap" in body and "transcribed prose" in body
    assert b.coverage_status == CoverageStatus.FLAGGED
    assert ctx.flags and "OCR uncertain" in ctx.flags[0].reason


def test_clean_vlm_text_decodes_escaped_newlines():
    from pdf2md.describe import clean_vlm_text

    # a model that returns one \n-joined line -> decode so the markdown renders
    escaped = "# Title\\n\\nBy Eastman N. Jacobs\\nLangley Memorial"
    out, _ = clean_vlm_text(escaped)
    assert out == "# Title\n\nBy Eastman N. Jacobs\nLangley Memorial"
    # genuine multi-line output is untouched; unicode is not mangled
    assert clean_vlm_text("# Title\n\nBody with Δ and café")[0] == "# Title\n\nBody with Δ and café"


def test_table_crops_key_on_type_not_id():
    from pdf2md.pipeline import _table_crops
    from pdf2md.schema import BBox, Block, BlockType, TableData

    bb = BBox(0, 10, 5, 0)
    real = Block("#/tables/1", BlockType.TABLE, "", 1, bbox=bb, extra={"ocr": True})
    # --ocr-page-vlm repurposes a table block's id into the page transcription; keying on the
    # "#/tables/" id prefix (the old bug) would crop it and lose the text. Keying on type must not.
    transcript = Block("#/tables/2", BlockType.PARAGRAPH, "# Page\n\ntext", 1, bbox=bb,
                       extra={"ocr": True, "text_source": "vlm-page"})
    tables = [TableData("#/tables/1", 1, bb, gfm="| a |"), TableData("#/tables/2", 1, bb, gfm="| b |")]
    selected, authoritative = _table_crops([real, transcript], tables)
    # The real (OCR-scan) table crops; the transcription is not a table any more.
    assert {b.id for b in selected} == {"#/tables/1"}
    assert authoritative == {"#/tables/1"}

    digital = Block("#/tables/3", BlockType.TABLE, "", 1, bbox=bb)
    tables.append(TableData(digital.id, 1, bb, gfm="| c |"))
    # Every table gets a crop; only the scan's is the authority over its cells.
    selected, authoritative = _table_crops([real, transcript, digital], tables)
    assert {b.id for b in selected} == {"#/tables/1", "#/tables/3"}
    assert authoritative == {"#/tables/1"}
    # --table-ocr reads the crop for every table, so every crop is authoritative.
    _, authoritative = _table_crops(
        [real, transcript, digital], tables, include_structured=True
    )
    assert authoritative == {"#/tables/1", "#/tables/3"}


def test_table_crops_include_glyph_unbacked_tables():
    from pdf2md.pipeline import _table_crops
    from pdf2md.schema import BBox, Block, BlockType, TableData

    bb = BBox(0, 10, 5, 0)
    raster_read = Block("#/tables/9", BlockType.TABLE, "", 2, bbox=bb)
    clean = Block("#/tables/10", BlockType.TABLE, "", 2, bbox=bb)
    tables = [
        TableData("#/tables/9", 2, bb, gfm="| a |",
                  cell_glyph_check={"cells": {"engine_without_glyphs": 30}}),
        TableData("#/tables/10", 2, bb, gfm="| b |",
                  cell_glyph_check={"cells": {"exact": 30}}),
    ]
    selected, authoritative = _table_crops([raster_read, clean], tables)
    assert {b.id for b in selected} == {"#/tables/9", "#/tables/10"}
    assert authoritative == {"#/tables/9"}
    assert raster_read.extra["cells_unverified"] is True
    assert "cells_unverified" not in clean.extra
