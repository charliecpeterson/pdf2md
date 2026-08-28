"""The selective third reader resolves only a two-of-three candidate match."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "eval_reader_cascade",
    SCRIPTS / "eval_reader_cascade.py",
)
cascade = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(cascade)
finally:
    sys.path.pop(0)


def _record(
    row: int,
    expected: str,
    primary: str | None,
    reader: str | None,
    *,
    basis: str | None = None,
) -> dict[str, object]:
    return {
        "source_sha256": "a" * 64,
        "page": 1,
        "block_id": "#/table",
        "row": row,
        "column": 1,
        "expected": expected,
        "actual": primary,
        "reference_actual": reader,
        "resolution_basis": basis,
    }


def _adjudicator(row: int, value: str | None) -> dict[str, object]:
    record = _record(row, "unused", "unused", value)
    return record


def test_selective_cascade_resolves_only_supported_candidate():
    primary = {"records": [
        _record(0, "-0.7795", "-0.1795", "-0.7795"),
        _record(1, "1.0", "1.0", "2.0"),
        _record(2, "3.0", "3.0", "4.0"),
        _record(3, "5.0", "5.0", "6.0", basis="local_continuity_primary"),
        _record(4, "7.0", "7.0", "7.0"),
    ]}
    adjudicator = {"records": [
        _adjudicator(0, "-0.7795"),
        _adjudicator(1, "1.0"),
        _adjudicator(2, "9.0"),
        _adjudicator(3, "6.0"),
        _adjudicator(4, "8.0"),
    ]}

    report = cascade.evaluate(primary, adjudicator)

    assert report["checked"] == 5
    assert report["triggered"] == 4
    assert report["avoided_adjudicator_calls"] == 1
    assert report["agree"] == 5
    assert report["disagree"] == 0
    assert report["corrections"] == 1
    assert report["regressions"] == 0
    assert report["statuses"] == {
        "reader_agreement": 1,
        "reader_refused": 0,
        "primary_missing": 0,
        "majority_primary": 1,
        "majority_reader": 1,
        "adjudicator_refused": 0,
        "adjudicator_third_value": 1,
        "validator_rejected": 1,
    }
    assert [record["selected"] for record in report["records"]] == [
        "-0.7795", "1.0", "3.0", "5.0", "7.0"
    ]


def test_selective_cascade_records_refusals_and_missing_primary():
    primary = {"records": [
        _record(0, "1.0", "1.0", None),
        _record(1, "2.0", "1.0", "2.0"),
        _record(2, "3.0", None, "3.0"),
    ]}

    report = cascade.evaluate(primary, {"records": []})

    assert report["statuses"]["reader_refused"] == 1
    assert report["statuses"]["adjudicator_refused"] == 1
    assert report["statuses"]["primary_missing"] == 1
    assert report["agree"] == 1
    assert report["disagree"] == 1
    assert report["tool_refused"] == 1
