"""MinerU native output translates at the engine seam without importing MinerU."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest

from pdf2md.engines.mineru import MinerUEngine
from pdf2md.engines.mineru import (
    _capture_output,
    _progress_counter,
    _table_markup,
    _translate_middle,
)
from pdf2md.logging import Progress
from pdf2md.schema import BlockType


def test_table_markup_preserves_cells_math_and_spans():
    gfm, spanning = _table_markup(
        '<table><tr><th>A</th><th>B</th></tr>'
        '<tr><td rowspan="2">1</td><td><eq>g_J</eq></td></tr>'
        '<tr><td>x</td></tr></table>'
    )

    assert spanning is True
    assert "| A | B |" in gfm
    assert "| 1 | $g_J$ |" in gfm


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

    with pytest.raises(RuntimeError):
        engine.convert(Path("source.pdf"))

    assert invocation["env"]["MINERU_TASK_RESULT_TIMEOUT_SECONDS"] == "28800"


def test_cache_identity_includes_deskew_policy():
    engine = MinerUEngine.__new__(MinerUEngine)
    engine.executable = "/opt/mineru"
    engine.version = "mineru 3.4.4"
    engine.deskew_scans = True
    enabled = engine.cache_identity()

    engine.deskew_scans = False

    assert enabled != engine.cache_identity()


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
