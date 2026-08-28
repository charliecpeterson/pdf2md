"""MinerU repeat matching uses page, block type, and source geometry."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).parent.parent
SPEC = importlib.util.spec_from_file_location(
    "eval_mineru_repeat_stability",
    ROOT / "scripts" / "eval_mineru_repeat_stability.py",
)
stability = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stability)


def _run(identifier: str, bbox: tuple[float, float, float, float], content: str):
    return {
        "id": identifier,
        "sha256": identifier * 64,
        "records": [{
            "id": "#/table/1",
            "page": 1,
            "type": "table",
            "bbox": bbox,
            "content_sha256": content,
        }],
    }


def test_pairwise_separates_geometry_recall_from_content_identity():
    reference = _run("a", (0, 0, 100, 100), "first")
    candidate = _run("b", (1, 1, 101, 101), "second")

    pair = stability._pairwise(reference, candidate)

    assert pair["matched_blocks"] == 1
    assert pair["block_recall_at_iou_0_9"] == 1.0
    assert pair["block_precision_at_iou_0_9"] == 1.0
    assert pair["matched_content_identical"] == 0
    assert pair["matched_content_identity_rate"] == 0.0
    assert pair["matched_table_content_identical"] == 0
    assert pair["matched_table_content_identity_rate"] == 0.0
    assert pair["changed_content_by_type"] == {"table": 1}
    assert pair["exact_json"] is False


def test_iou_refuses_nonoverlapping_boxes():
    assert stability._iou((0, 0, 10, 10), (11, 0, 21, 10)) == 0.0


def test_pinned_repeat_corpus_matches_raw_outputs():
    report = stability.evaluate(
        ROOT, ROOT / "tests" / "mineru_repeat_stability_sources.json"
    )

    assert stability.check_corpus(
        ROOT, ROOT / "tests" / "mineru_repeat_stability_corpus.json", report
    )
