"""MinerU native output translates at the engine seam without importing MinerU."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import time
from pathlib import Path

import pytest

from pdf2md.engines.mineru import (
    MinerUEngine,
    _capture_output,
    _progress_counter,
    _translate_middle,
)
from pdf2md.logging import Progress
from pdf2md.schema import BlockType


def test_translate_middle_preserves_order_equations_tables_and_figures():
    document = {
        "pdf_info": [{
            "page_idx": 0,
            "page_size": [400, 600],
            "preproc_blocks": [
                {
                    "type": "text", "bbox": [10, 20, 390, 50],
                    "lines": [{"spans": [
                        {"type": "text", "content": "Energy is"},
                        {"type": "inline_equation", "content": "E=mc^2"},
                    ]}],
                },
                {
                    "type": "interline_equation", "bbox": [100, 60, 300, 90],
                    "lines": [{"spans": [{
                        "type": "interline_equation", "content": "E=mc^2 \\tag{1}",
                    }]}],
                },
                {
                    "type": "table", "bbox": [20, 100, 380, 200],
                    "blocks": [
                        {"type": "table_caption", "bbox": [20, 90, 200, 100],
                         "lines": [{"spans": [{"type": "text", "content": "Table 1"}]}]},
                        {"type": "table_body", "lines": [{"spans": [{
                            "type": "table",
                            "html": "<table><tr><td>A</td><td>B</td></tr>"
                                    "<tr><td>1</td><td>x</td></tr></table>",
                        }]}]},
                    ],
                },
                {
                    "type": "image", "bbox": [40, 220, 200, 360],
                    "blocks": [
                        {"type": "image_body", "lines": [{"spans": [{
                            "type": "image", "content": "x axis",
                        }]}]},
                        {"type": "image_caption", "bbox": [40, 360, 200, 380],
                         "lines": [{"spans": [{"type": "text", "content": "Figure 1"}]}]},
                    ],
                },
            ],
        }],
    }

    result = _translate_middle(document, "mineru 3.4.4")

    assert [block.type for block in result.blocks] == [
        BlockType.PARAGRAPH,
        BlockType.EQUATION,
        BlockType.CAPTION,
        BlockType.TABLE,
        BlockType.FIGURE,
    ]
    assert result.blocks[0].text == "Energy is $E=mc^2$"
    assert result.blocks[1].bbox.y0 == 540
    assert result.tables[0].gfm == "| A | B |\n|---|---|\n| 1 | x |"
    assert result.figures[0].caption == "Figure 1"
    assert result.figures[0].labels.text == "x axis"
    assert result.engine_versions["mineru"] == "mineru 3.4.4"


def _lines(text):
    return [{"spans": [{"type": "text", "content": text}]}]


def test_translate_middle_keeps_every_table_caption_and_footnote():
    """The shape MinerU 3.4.5 gives a Critical Stability Constants table: two captions,
    the body, then footnotes holding the conditions behind its value markers."""
    document = {"pdf_info": [{
        "page_idx": 0,
        "page_size": [600, 800],
        "preproc_blocks": [
            {"type": "table", "bbox": [10, 10, 500, 300], "blocks": [
                {"type": "table_caption", "bbox": [10, 10, 300, 20], "lines": _lines("Ligand A")},
                {"type": "table_caption", "bbox": [10, 22, 320, 32],
                 "lines": _lines("(Other values in Vol. 9)")},
                {"type": "table_body", "bbox": [10, 40, 500, 240], "lines": [{"spans": [{
                    "type": "table", "html": "<table><tr><td>2.66<sup>h</sup></td></tr></table>",
                }]}]},
                {"type": "table_footnote", "bbox": [10, 250, 300, 260],
                 "lines": _lines("b 25°, 0.5; h 20°, 0.1")},
                {"type": "table_footnote", "bbox": [10, 262, 300, 272],
                 "lines": _lines("u NaClO4 used as background electrolyte")},
                {"type": "table_footnote", "bbox": [10, 274, 300, 284],
                 "lines": _lines("Bibliography: 99XY")},
            ]},
            {"type": "image", "bbox": [10, 320, 300, 500], "blocks": [
                {"type": "image_body", "bbox": [10, 320, 300, 480], "lines": []},
                {"type": "image_footnote", "bbox": [10, 482, 300, 492],
                 "lines": _lines("Drawn from the crystal structure")},
            ]},
        ],
    }]}

    result = _translate_middle(document)

    assert [(block.type, block.text) for block in result.blocks] == [
        (BlockType.CAPTION, "Ligand A\n(Other values in Vol. 9)"),
        (BlockType.TABLE, ""),
        (BlockType.FOOTNOTE, "b 25°, 0.5; h 20°, 0.1"),
        (BlockType.FOOTNOTE, "u NaClO4 used as background electrolyte"),
        (BlockType.FOOTNOTE, "Bibliography: 99XY"),
        (BlockType.FIGURE, ""),
        (BlockType.FOOTNOTE, "Drawn from the crystal structure"),
    ]
    caption = result.blocks[0].bbox
    assert (caption.x0, caption.y0, caption.x1, caption.y1) == (10, 790, 320, 768)
    footnote = result.blocks[2].bbox
    assert (footnote.x0, footnote.y0, footnote.x1, footnote.y1) == (10, 550, 300, 540)


def test_translate_middle_keeps_an_unmapped_kind_as_text_and_says_so(caplog):
    document = {"pdf_info": [{
        "page_idx": 0,
        "page_size": [600, 800],
        "preproc_blocks": [
            {"type": "aside_text", "bbox": [500, 10, 590, 100], "lines": _lines("Margin note")},
        ],
    }]}

    with caplog.at_level("WARNING", logger="pdf2md"):
        result = _translate_middle(document)

    assert [(block.type, block.text) for block in result.blocks] == [
        (BlockType.OTHER, "Margin note"),
    ]
    assert result.blocks[0].extra == {"mineru_type": "aside_text"}
    assert "kept as plain text: aside_text (1)" in caplog.text


def test_translate_middle_refuses_output_without_a_pdf_info_page_list():
    with pytest.raises(RuntimeError, match=r"no `pdf_info` page list \(keys: \['pages'\]\)"):
        _translate_middle({"pages": []})


def test_convert_keeps_the_native_middle_json_and_what_produced_it(tmp_path):
    middle = {"pdf_info": [{"page_idx": 0, "page_size": [600, 800], "preproc_blocks": [
        {"type": "text", "bbox": [10, 10, 200, 30], "lines": _lines("Hello")},
    ]}]}
    script = tmp_path / "mineru"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "if sys.argv[1] == '--version':\n"
        "    print('mineru, version 9.9.9'); raise SystemExit(0)\n"
        "out = pathlib.Path(sys.argv[sys.argv.index('-o') + 1]) / 'book' / 'hybrid_ocr'\n"
        "out.mkdir(parents=True)\n"
        f"(out / 'book_middle.json').write_text({json.dumps(middle)!r})\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 stand-in")

    engine = MinerUEngine(str(script), deskew_scans=False, force_ocr=True)
    result = engine.convert(pdf)

    assert [block.text for block in result.blocks] == ["Hello"]
    assert json.loads(result.native_output["mineru-middle.json"]) == middle
    assert json.loads(result.native_output["mineru-run.json"]) == {
        "mineru_version": "mineru, version 9.9.9",
        "arguments": ["-b", "hybrid-engine", "--effort", "high", "--image-analysis", "true",
                      "-m", "ocr"],
        "input_sha256": hashlib.sha256(b"%PDF-1.4 stand-in").hexdigest(),
        "deskew_angles": {},
    }


def test_native_output_is_written_gzipped_and_reproducibly(tmp_path):
    from pdf2md.engine_state import write_native_output
    from pdf2md.engines.base import EngineResult

    result = EngineResult([], [], [], {}, native_output={"mineru-middle.json": b'{"pdf_info": []}'})

    first = write_native_output(tmp_path / "a", result)
    second = write_native_output(tmp_path / "b", result)

    assert [path.relative_to(tmp_path / "a").as_posix() for path in first] == [
        "engine-native/mineru-middle.json.gz",
    ]
    assert gzip.decompress(first[0].read_bytes()) == b'{"pdf_info": []}'
    assert first[0].read_bytes() == second[0].read_bytes()
    assert write_native_output(tmp_path / "c", EngineResult([], [], [], {})) == []
    assert not (tmp_path / "c").exists()


def test_convert_reports_only_bounded_command_output_tail(monkeypatch):
    class FailedProcess:
        stdout = [f"line {index}\n" for index in range(60)]

        @staticmethod
        def wait():
            return 7

    invocation = {}

    def failed_process(*args, **kwargs):
        invocation.update(kwargs)
        return FailedProcess()

    monkeypatch.setattr("pdf2md.engines.mineru.subprocess.Popen", failed_process)
    monkeypatch.delenv("MINERU_TASK_RESULT_TIMEOUT_SECONDS", raising=False)
    engine = MinerUEngine.__new__(MinerUEngine)
    engine.executable = "mineru"
    engine.deskew_scans = False
    engine.force_ocr = False

    with pytest.raises(RuntimeError) as error:
        engine.convert(Path("source.pdf"))

    lines = str(error.value).splitlines()
    assert lines[0] == "MinerU failed with exit code 7: line 10"
    assert lines[-1] == "line 59"
    assert len(lines) == 50
    assert invocation["env"]["MINERU_TASK_RESULT_TIMEOUT_SECONDS"] == "21600"


def test_convert_preserves_explicit_mineru_task_timeout(monkeypatch):
    class FailedProcess:
        stdout = []

        @staticmethod
        def wait():
            return 1

    invocation = {}

    def failed_process(*args, **kwargs):
        invocation.update(kwargs)
        return FailedProcess()

    monkeypatch.setattr("pdf2md.engines.mineru.subprocess.Popen", failed_process)
    monkeypatch.setenv("MINERU_TASK_RESULT_TIMEOUT_SECONDS", "28800")
    engine = MinerUEngine.__new__(MinerUEngine)
    engine.executable = "mineru"
    engine.deskew_scans = False
    engine.force_ocr = False

    with pytest.raises(RuntimeError):
        engine.convert(Path("source.pdf"))

    assert invocation["env"]["MINERU_TASK_RESULT_TIMEOUT_SECONDS"] == "28800"


def test_cache_identity_includes_deskew_policy():
    engine = MinerUEngine.__new__(MinerUEngine)
    engine.executable = "/opt/mineru"
    engine.version = "mineru 3.4.4"
    engine.deskew_scans = True
    engine.force_ocr = False
    enabled = engine.cache_identity()

    engine.deskew_scans = False

    assert enabled != engine.cache_identity()


@pytest.mark.parametrize("force_ocr", [False, True])
def test_force_ocr_has_mineru_read_page_pixels(monkeypatch, force_ocr):
    class FailedProcess:
        stdout = []

        @staticmethod
        def wait():
            return 1

    invocation = {}

    def failed_process(command, **kwargs):
        invocation["command"] = command
        return FailedProcess()

    monkeypatch.setattr("pdf2md.engines.mineru.subprocess.Popen", failed_process)
    engine = MinerUEngine.__new__(MinerUEngine)
    engine.executable = "mineru"
    engine.deskew_scans = False
    engine.force_ocr = force_ocr

    with pytest.raises(RuntimeError):
        engine.convert(Path("source.pdf"))

    method = ["-m", "ocr"] if force_ocr else []
    assert invocation["command"][:3] == ["mineru", "-p", "source.pdf"]
    assert invocation["command"][5:] == [
        "-b", "hybrid-engine", "--effort", "high", "--image-analysis", "true", *method,
    ]


def test_select_engine_hands_force_ocr_to_mineru(tmp_path):
    from pdf2md.config import Config
    from pdf2md.engines.select import select_engine

    script = tmp_path / "mineru"
    script.write_text("#!/bin/sh\necho 'mineru, version 9.9.9'\n", encoding="utf-8")
    script.chmod(0o755)

    engine = select_engine(
        None, Config(engine="mineru", mineru_executable=str(script), force_ocr=True)
    )

    assert engine.force_ocr is True
    assert engine.cache_identity() == (
        f"{script.resolve()}:mineru, version 9.9.9:deskew=True:force_ocr=True"
    )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "Hybrid processing window 1/2",
            ("MinerU processing windows", 1, 2, "windows"),
        ),
        (
            "Layout Predict: 42%|#### | 42/99 [01:20<01:48]",
            ("MinerU Layout Predict", 42, 99, "items"),
        ),
        (
            "\x1b[32mTable Recognition: 12%|# | 6/50\x1b[0m",
            ("MinerU Table Recognition", 6, 50, "items"),
        ),
        ("ordinary MinerU log message", None),
    ],
)
def test_progress_counter_reads_only_real_mineru_counts(message, expected):
    assert _progress_counter(message) == expected


def test_capture_output_reports_heartbeat_during_silent_stage(caplog):
    class SlowOutput:
        def __iter__(self):
            yield "Layout Predict: 1/1\n"
            time.sleep(0.03)
            yield "recognition complete\n"

    class Process:
        stdout = SlowOutput()

    logger = logging.getLogger("pdf2md.test.mineru-heartbeat")
    with caplog.at_level(logging.INFO, logger=logger.name):
        tail = _capture_output(Process(), Progress(logger), heartbeat_seconds=0.005)

    assert list(tail) == ["Layout Predict: 1/1", "recognition complete"]
    assert "MinerU still working (no new engine output)" in caplog.text
