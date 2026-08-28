"""The promotion synthesis preserves evidence authority and the frozen refusal."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location(
    "eval_promotion_decision",
    ROOT / "scripts" / "eval_promotion_decision.py",
)
promotion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(promotion)


def test_pinned_promotion_decision_keeps_non_authoritative_signals_separate():
    report = promotion.evaluate(ROOT, promotion.DEFAULT_SOURCES)

    assert promotion.check_corpus(ROOT, promotion.DEFAULT_CORPUS, report)
    assert report["decision"] == {
        "automatic_ocr_value_promotion": "not_defined",
        "exact_external_reference_override": (
            "eligible_when_supplied_and_semantically_mapped"
        ),
        "new_external_adapter": "wait_for_semantically_overlapping_source",
        "review_default": "retain_confidence_stratified_default",
        "rendering_instability": "review_ranking_only",
        "internal_scientific_checks": "support_or_review_only",
        "experimental_column_locators": "evaluation_only",
        "next_required_evidence": (
            "more held-out proposed corrections or an independent source with "
            "overlapping semantic fields"
        ),
    }
    assert report["evidence"]["fixed_reader_score_0_99"][
        "wrong_replacement_rate_upper_95"
    ] == 0.65761977
    assert report["evidence"]["learned_threshold_heldout_wrong"] == 1
    assert report["evidence"]["external_reference_audit"][
        "candidates_with_matching_extracted_fields"
    ] == 0


def test_promotion_decision_rejects_artifact_hash_drift(tmp_path):
    sources = json.loads(promotion.DEFAULT_SOURCES.read_text())
    sources["artifacts"]["numeric_confidence"]["sha256"] = "0" * 64
    sources_path = tmp_path / "sources.json"
    sources_path.write_text(json.dumps(sources))

    with pytest.raises(ValueError, match="numeric_confidence"):
        promotion.evaluate(ROOT, sources_path)
