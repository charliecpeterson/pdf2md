"""VLM digitize consensus: binned-median aggregation across sampled reads,
convergence early-stop, and the scatter-like fallback. Fake describer, no model."""

from __future__ import annotations

import json

import pytest

from pdf2md.digitize_vlm import vlm_digitize_consensus
from pdf2md.schema import Digitization


def _reply(points_per_series: list[list[tuple[float, float]]]) -> str:
    return json.dumps({"series": [
        {"label": f"s{i}", "points": [[x, y] for x, y in pts]}
        for i, pts in enumerate(points_per_series)
    ]})


class _FakeDescriber:
    """Returns scripted replies in order; records describe() calls."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    def describe(self, image_path, kind, context="", **kwargs):
        self.calls.append(kwargs)
        if not self.replies:
            raise AssertionError("describe called more times than replies scripted")
        return self.replies.pop(0)


def test_median_curve_and_dispersion_recorded():
    # Three draws of one line, each with a small y offset; the median lands on the
    # true line and the dispersion reflects the spread.
    truth = [(0.0, 0.0), (1.0, 1.0)]
    offsets = [-0.1, 0.0, 0.1]
    describer = _FakeDescriber([
        _reply([[(x, y + o) for x, y in truth]]) for o in offsets
    ])
    result = vlm_digitize_consensus("crop.png", describer, votes=3, temperature=0.4)
    assert result.consensus_votes == 3
    assert result.method == "vlm-consensus"
    ys = [y for _, y in result.series[0]]
    assert all(abs(y - x) < 0.05 for x, y in zip([p[0] for p in result.series[0]], ys))
    assert result.dispersion is not None and 0.01 <= result.dispersion <= 0.2
    assert "3-vote median consensus" in result.note
    # votes after the first sample at the consensus temperature...
    assert any(kw.get("temperature") == 0.4 for kw in describer.calls)
    # ...while vote 0 rides the endpoint default (byte-identical single read).
    assert describer.calls[0].get("temperature") is None


def test_convergence_stops_early_when_draws_agree():
    identical = [_reply([[(x, x / 10) for x in range(10)]]) for _ in range(3)]
    describer = _FakeDescriber(identical)
    result = vlm_digitize_consensus("crop.png", describer, votes=6, temperature=0.4)
    # Identical draws converge at the minimum-vote threshold; votes 4-6 never fire.
    assert len(describer.calls) == 3
    assert result.consensus_votes == 3


def test_scatter_like_point_sets_fall_back_to_best_read():
    # Multi-y at the same x is not interpolable: keep the highest-confidence read,
    # flagged, rather than median-smoothing distinct branches.
    circle = [(0.5, 0.0), (0.6, 0.9), (0.6, -0.9), (0.5, 1.0)]
    replies = [_reply([circle]), _reply([circle])]
    describer = _FakeDescriber(replies)
    result = vlm_digitize_consensus("crop.png", describer, votes=2)
    assert result.consensus_votes == 2
    assert "did not align" in result.note
    assert [len(s) for s in result.series] == [len(circle)]


def test_single_vote_passthrough_keeps_flat_note():
    describer = _FakeDescriber([_reply([[(0.0, 1.0), (2.0, 3.0)]])])
    result = vlm_digitize_consensus("crop.png", describer, votes=1)
    assert result.consensus_votes == 1
    assert result.dispersion is None and result.method != "vlm-consensus"
    assert len(describer.calls) == 1


def test_all_reads_empty_returns_none():
    describer = _FakeDescriber(["not json at all", ""])
    assert vlm_digitize_consensus("crop.png", describer, votes=2) is None
