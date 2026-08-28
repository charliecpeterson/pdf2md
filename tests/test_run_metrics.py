"""Run metrics keep exact sequential accounting without sleeping in tests."""

import json

from pdf2md.run_metrics import (
    RunMetrics,
    _rss_bytes,
    compare_run_metrics,
    failed_optional_calls,
    load_run_metrics,
)


def test_run_metrics_sum_stages_and_keep_work_counts():
    ticks = iter((10.0, 11.25, 13.5, 14.0))
    metrics = RunMetrics(
        clock=lambda: next(ticks),
        memory_reader=lambda: {
            "available": True,
            "scope": "process-lifetime high-water marks",
            "main_process_peak_rss_bytes": 4096,
            "largest_terminated_child_peak_rss_bytes": 0,
        },
    )

    metrics.finish("parse", pages=12, blocks=80)
    metrics.finish("charts", attempted=5, accepted=2, declined=3, failed=0)
    metrics.finish("emit", files=4)

    assert metrics.report() == {
        "duration_s": 4.0,
        "stages": {
            "parse": {"duration_s": 1.25, "counts": {"pages": 12, "blocks": 80}},
            "charts": {
                "duration_s": 2.25,
                "counts": {"attempted": 5, "accepted": 2, "declined": 3, "failed": 0},
            },
            "emit": {"duration_s": 0.5, "counts": {"files": 4}},
        },
        "memory": {
            "available": True,
            "scope": "process-lifetime high-water marks",
            "main_process_peak_rss_bytes": 4096,
            "largest_terminated_child_peak_rss_bytes": 0,
        },
    }


def test_peak_rss_units_match_supported_platform_contracts():
    assert _rss_bytes(4096, "darwin") == 4096
    assert _rss_bytes(4, "linux") == 4096


def test_failed_optional_calls_only_counts_retry_exhaustion():
    metrics = {
        "stages": {
            "charts": {"counts": {"failed": 4, "vision_failures": 1}},
            "descriptions": {"counts": {"vision_failures": 2}},
            "emit": {"counts": {"dropped": 3}},
        }
    }

    assert failed_optional_calls(metrics) == 3
    assert failed_optional_calls({}) == 0


def test_compare_run_metrics_reports_time_and_work_changes(tmp_path):
    before = {
        "duration_s": 10.0,
        "memory": {
            "available": True,
            "main_process_peak_rss_bytes": 100,
            "largest_terminated_child_peak_rss_bytes": 0,
        },
        "stages": {
            "parse": {"duration_s": 8.0, "counts": {"pages": 20}},
            "charts": {"duration_s": 2.0, "counts": {"attempted": 5}},
        },
    }
    after = {
        "duration_s": 7.0,
        "memory": {
            "available": True,
            "main_process_peak_rss_bytes": 80,
            "largest_terminated_child_peak_rss_bytes": 0,
        },
        "stages": {
            "parse": {"duration_s": 6.0, "counts": {"pages": 20}},
            "charts": {"duration_s": 1.0, "counts": {"attempted": 5}},
        },
    }
    version_dir = tmp_path / "v2"
    version_dir.mkdir()
    (version_dir / "provenance.json").write_text(
        json.dumps({"provenance": {"run_metrics": after}})
    )

    comparison = compare_run_metrics(before, load_run_metrics(version_dir))

    assert comparison["duration"] == {
        "before_s": 10.0,
        "after_s": 7.0,
        "delta_s": -3.0,
        "change_percent": -30.0,
    }
    assert comparison["memory"]["main_process_peak_rss_bytes"] == {
        "before_bytes": 100,
        "after_bytes": 80,
        "delta_bytes": -20,
        "change_percent": -20.0,
    }
    assert comparison["stages"][1] == {
        "stage": "charts",
        "before_s": 2.0,
        "after_s": 1.0,
        "delta_s": -1.0,
        "change_percent": -50.0,
        "before_counts": {"attempted": 5},
        "after_counts": {"attempted": 5},
    }
