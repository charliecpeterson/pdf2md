"""ATSP calculations provide scientific support without replacing printed digits."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parent.parent
SCRIPTS = ROOT / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


atsp = _load("eval_atsp_fischer_reference")
runner = _load("run_atsp_hf_reference")


def test_atsp_input_preserves_fixed_closed_shell_fields():
    case = {
        "symbol": "O",
        "term": "3P",
        "atomic_number": 8,
        "closed_shells": ["1s", "2s"],
        "open_configuration": "2p(4)",
    }

    assert runner._input(case).splitlines()[:3] == [
        "O,3P,8",
        "  1s  2s",
        "2p(4)",
    ]


def test_atsp_log_parser_recovers_orbital_and_total_values():
    values = atsp._parse_hf_log(
        atsp.DEFAULT_RUN / "o-1s2-2s2-2p4-3p" / "hf.log"
    )

    assert values[("1s", "E")] == "41.3373140"
    assert values[("2p", "1/R**3")] == "4.9741"
    assert values[("TOTAL ENERGY =", "value")] == "-74.80939845"


def test_pinned_atsp_cross_check_is_support_only_and_finds_missing_cell():
    report = atsp.evaluate(atsp.DEFAULT_VERSION, atsp.DEFAULT_CASES, atsp.DEFAULT_RUN)

    assert atsp.check_corpus(ROOT, atsp.DEFAULT_CORPUS, report)
    assert report["scientific_support"] == {
        "agree": 152,
        "disagree": 0,
        "tool_refused": 1,
    }
    refused = [record for record in report["records"] if record["outcome"] == "tool_refused"]
    assert refused == [{
        "case_id": "b-1s2-2s2-2p1-2p",
        "atomic_number": 5,
        "symbol": "B",
        "term": "2P",
        "row_key": "2p",
        "column": "1/R**3",
        "atsp_value": "0.7756",
        "extracted_value": None,
        "absolute_delta": None,
        "relative_delta": None,
        "outcome": "tool_refused",
        "verification_status": "scientific_support",
    }]
    assert all(
        record["verification_status"] != "externally_verified"
        for record in report["records"]
    )


def test_atsp_cross_check_rejects_corpus_artifact_drift(tmp_path):
    corpus = json.loads(atsp.DEFAULT_CORPUS.read_text())
    corpus["artifacts"]["run_manifest"]["sha256"] = "0" * 64
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(corpus))

    with pytest.raises(ValueError, match="run_manifest"):
        atsp.check_corpus(
            ROOT,
            path,
            atsp.evaluate(atsp.DEFAULT_VERSION, atsp.DEFAULT_CASES, atsp.DEFAULT_RUN),
        )
