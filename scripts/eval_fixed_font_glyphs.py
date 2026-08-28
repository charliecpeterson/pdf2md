"""Test whether agreed table cells can teach a document-specific glyph reader.

The atlas is built only from independent-reader agreement. Gold labels score the
result but never select templates or candidate values.
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

from pdf2md.table_verify import _numeric_read, typed_value


def _key(record: dict, *, crop: bool = False) -> tuple[str, str, int, int]:
    return (
        record["source_sha256"],
        record["block_id"],
        record["source_row" if crop else "row"],
        record["source_column" if crop else "column"],
    )


def _equivalent(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return False
    if left == right:
        return True
    if typed_value(left)[2] == typed_value(right)[2] == "numeric":
        return _numeric_read(left) == _numeric_read(right)
    return False


def _candidate_value(value: str) -> str:
    return _numeric_read(value) if typed_value(value)[2] == "numeric" else value


def _ink(path: Path) -> np.ndarray:
    gray = np.asarray(Image.open(path).convert("L"))
    histogram = np.bincount(gray.ravel(), minlength=256).astype(float)
    total = gray.size
    weighted_total = float(np.dot(np.arange(256), histogram))
    background_weight = 0.0
    background_sum = 0.0
    best_variance = -1.0
    threshold = 127
    for value, count in enumerate(histogram):
        background_weight += count
        if not background_weight:
            continue
        foreground_weight = total - background_weight
        if not foreground_weight:
            break
        background_sum += value * count
        background_mean = background_sum / background_weight
        foreground_mean = (weighted_total - background_sum) / foreground_weight
        variance = background_weight * foreground_weight * (
            background_mean - foreground_mean
        ) ** 2
        if variance > best_variance:
            best_variance = variance
            threshold = value
    ink = gray <= threshold
    ink[ink.mean(axis=1) >= 0.8] = False
    return ink


def _occupied_runs(ink: np.ndarray) -> list[tuple[int, int]]:
    runs = []
    start = None
    for column, occupied in enumerate(ink.any(axis=0)):
        if occupied and start is None:
            start = column
        elif not occupied and start is not None:
            runs.append((start, column))
            start = None
    if start is not None:
        runs.append((start, ink.shape[1]))
    return runs


def _group_runs(
    runs: list[tuple[int, int]], count: int, width: int
) -> list[tuple[int, int]] | None:
    runs = list(runs)
    while runs and runs[0][0] == 0 and runs[0][1] < width * 0.1:
        runs.pop(0)
    while runs and runs[-1][1] == width and width - runs[-1][0] < width * 0.1:
        runs.pop()
    if len(runs) == 1 and count == 2:
        left, right = runs[0]
        middle = (left + right) // 2
        runs = [(left, middle), (middle, right)]
    if len(runs) < count:
        return None
    if len(runs) == count:
        return runs

    best = None
    for cuts in itertools.combinations(range(1, len(runs)), count - 1):
        bounds = (0, *cuts, len(runs))
        groups = [
            (runs[bounds[index]][0], runs[bounds[index + 1] - 1][1])
            for index in range(count)
        ]
        centers = [(left + right) / 2 for left, right in groups]
        steps = [right - left for left, right in zip(centers, centers[1:])]
        spacing_cost = statistics.pstdev(steps) / max(statistics.mean(steps), 1)
        largest_group = max(
            bounds[index + 1] - bounds[index] for index in range(count)
        )
        score = spacing_cost + max(0, largest_group - 2)
        if best is None or score < best[0]:
            best = score, groups
    return best[1] if best else None


def _normalized_glyph(ink: np.ndarray) -> np.ndarray:
    occupied_rows = np.flatnonzero(ink.any(axis=1))
    ink = ink[occupied_rows[0] : occupied_rows[-1] + 1]
    height, width = ink.shape
    scale = min(44 / width, 60 / height)
    size = max(1, round(width * scale)), max(1, round(height * scale))
    resized = Image.fromarray(ink.astype(np.uint8) * 255).resize(
        size, Image.Resampling.LANCZOS
    )
    glyph = np.zeros((64, 48), dtype=np.float32)
    top = (64 - size[1]) // 2
    left = (48 - size[0]) // 2
    glyph[top : top + size[1], left : left + size[0]] = (
        np.asarray(resized, dtype=np.float32) / 255
    )
    return glyph


def _segment(path: Path, count: int) -> list[np.ndarray] | None:
    ink = _ink(path)
    groups = _group_runs(_occupied_runs(ink), count, ink.shape[1])
    if groups is None:
        return None
    return [_normalized_glyph(ink[:, left:right]) for left, right in groups]


def _atlas(cells: list[dict], *, omit: int | None = None) -> dict[str, list[np.ndarray]]:
    atlas = defaultdict(list)
    for index, cell in enumerate(cells):
        if index == omit:
            continue
        for character, glyph in zip(cell["value"], cell["glyphs"]):
            atlas[character].append(glyph)
    return dict(atlas)


def _glyph_distance(glyph: np.ndarray, templates: list[np.ndarray]) -> float:
    return min(float(np.mean(np.abs(glyph - template))) for template in templates)


def _candidate_score(
    candidate: str, glyphs: list[np.ndarray], atlas: dict[str, list[np.ndarray]]
) -> float | None:
    if len(candidate) != len(glyphs) or any(character not in atlas for character in candidate):
        return None
    return statistics.mean(
        _glyph_distance(glyph, atlas[character])
        for character, glyph in zip(candidate, glyphs)
    )


def evaluate(report_path: Path, crop_manifest_path: Path, source_sha256: str) -> dict:
    report = json.loads(report_path.read_text())
    manifest = json.loads(crop_manifest_path.read_text())
    crops = {_key(record, crop=True): record for record in manifest.get("crops", [])}
    records = [
        record for record in report.get("records", [])
        if record.get("source_sha256") == source_sha256
    ]

    agreed = []
    atlas_refusals = []
    for record in records:
        primary = record.get("actual")
        reference = record.get("reference_actual")
        if not _equivalent(primary, reference):
            continue
        value = _candidate_value(primary)
        crop = crops.get(_key(record))
        if crop is None:
            atlas_refusals.append({
                "block_id": record["block_id"],
                "row": record["row"],
                "column": record["column"],
                "value": value,
                "reason": "crop_missing",
            })
            continue
        glyphs = _segment(crop_manifest_path.parent / crop["path"], len(value))
        if glyphs is None:
            atlas_refusals.append({
                "block_id": record["block_id"],
                "row": record["row"],
                "column": record["column"],
                "value": value,
                "reason": "glyph_segmentation_failed",
            })
            continue
        agreed.append({"value": value, "glyphs": glyphs, "crop": crop["path"]})

    loo_records = []
    for index, cell in enumerate(agreed):
        atlas = _atlas(agreed, omit=index)
        missing = sorted(set(cell["value"]) - set(atlas))
        if missing:
            loo_records.append({
                "crop": cell["crop"],
                "expected": cell["value"],
                "predicted": None,
                "exact": False,
                "outcome": "tool_refused",
                "refusal_reason": "atlas_character_missing",
                "missing_characters": missing,
            })
            continue
        predicted = []
        for glyph in cell["glyphs"]:
            distances = {
                character: _glyph_distance(glyph, templates)
                for character, templates in atlas.items()
            }
            predicted.append(min(distances, key=distances.get) if distances else "")
        prediction = "".join(predicted)
        expected_score = _candidate_score(cell["value"], cell["glyphs"], atlas)
        alternatives = []
        for position, expected_character in enumerate(cell["value"]):
            for alternative_character in atlas:
                if alternative_character == expected_character:
                    continue
                candidate = (
                    cell["value"][:position]
                    + alternative_character
                    + cell["value"][position + 1:]
                )
                score = _candidate_score(candidate, cell["glyphs"], atlas)
                if score is not None:
                    alternatives.append((score, candidate))
        best_alternative_score, best_alternative = min(alternatives, default=(None, None))
        loo_records.append({
            "crop": cell["crop"],
            "expected": cell["value"],
            "predicted": prediction,
            "exact": prediction == cell["value"],
            "outcome": "agree" if prediction == cell["value"] else "disagree",
            "expected_score": round(expected_score, 6),
            "best_alternative": best_alternative,
            "best_alternative_score": (
                round(best_alternative_score, 6)
                if best_alternative_score is not None else None
            ),
            "score_margin": (
                round(best_alternative_score - expected_score, 6)
                if best_alternative_score is not None else None
            ),
        })

    atlas = _atlas(agreed)
    jackknife_atlases = [_atlas(agreed, omit=index) for index in range(len(agreed))]
    rankings = []
    for record in records:
        primary = record.get("actual")
        reference = record.get("reference_actual")
        if primary is None or reference is None or _equivalent(primary, reference):
            continue
        primary_candidate = _candidate_value(primary)
        reference_candidate = _candidate_value(reference)
        ranking = {
            "block_id": record["block_id"],
            "row": record["row"],
            "column": record["column"],
            "expected": record.get("expected"),
            "primary": primary,
            "reference": reference,
        }
        crop = crops.get(_key(record))
        if crop is None:
            ranking["refusal_reason"] = "crop_missing"
        elif len(primary_candidate) != len(reference_candidate):
            ranking["refusal_reason"] = "candidate_length_mismatch"
        else:
            glyphs = _segment(
                crop_manifest_path.parent / crop["path"], len(primary_candidate)
            )
            if glyphs is None:
                ranking["refusal_reason"] = "glyph_segmentation_failed"
            else:
                primary_score = _candidate_score(primary_candidate, glyphs, atlas)
                reference_score = _candidate_score(reference_candidate, glyphs, atlas)
                if primary_score is None or reference_score is None:
                    ranking["refusal_reason"] = "atlas_character_missing"
                else:
                    preferred = (
                        primary_candidate
                        if primary_score < reference_score
                        else reference_candidate
                    )
                    jackknife_preferences = {
                        "primary": 0,
                        "reference": 0,
                        "tool_refused": 0,
                    }
                    for trial_atlas in jackknife_atlases:
                        trial_primary_score = _candidate_score(
                            primary_candidate, glyphs, trial_atlas
                        )
                        trial_reference_score = _candidate_score(
                            reference_candidate, glyphs, trial_atlas
                        )
                        if trial_primary_score is None or trial_reference_score is None:
                            jackknife_preferences["tool_refused"] += 1
                        elif trial_primary_score < trial_reference_score:
                            jackknife_preferences["primary"] += 1
                        else:
                            jackknife_preferences["reference"] += 1
                    preferred_reader = (
                        "primary" if preferred == primary_candidate else "reference"
                    )
                    ranking.update({
                        "primary_score": round(primary_score, 6),
                        "reference_score": round(reference_score, 6),
                        "score_margin": round(abs(primary_score - reference_score), 6),
                        "preferred": preferred,
                        "jackknife_preferences": jackknife_preferences,
                        "jackknife_stable": (
                            jackknife_preferences[preferred_reader] == len(agreed)
                        ),
                        "preferred_correct": _equivalent(
                            preferred, record.get("expected")
                        ),
                        "outcome": (
                            "agree"
                            if _equivalent(preferred, record.get("expected"))
                            else "disagree"
                        ),
                    })
        if "preferred" not in ranking:
            ranking["outcome"] = "tool_refused"
        rankings.append(ranking)

    comparable_loo = [
        record for record in loo_records if record["outcome"] != "tool_refused"
    ]
    loo_characters = sum(len(record["expected"]) for record in comparable_loo)
    loo_correct_characters = sum(
        sum(left == right for left, right in zip(record["expected"], record["predicted"]))
        for record in comparable_loo
    )
    ranked = [record for record in rankings if "preferred" in record]
    loo_margins = [
        record["score_margin"] for record in loo_records
        if record.get("score_margin") is not None
    ]
    return {
        "schema_version": 1,
        "method": "document_specific_glyph_atlas_evaluation_only",
        "source_sha256": source_sha256,
        "reader_agreement_cells": len(agreed) + len(atlas_refusals),
        "atlas_cells": len(agreed),
        "atlas_refusals": atlas_refusals,
        "atlas_glyphs": {
            character: len(templates) for character, templates in sorted(atlas.items())
        },
        "leave_one_out": {
            "cells": len(loo_records),
            "exact_cells": sum(record["exact"] for record in loo_records),
            "agree": sum(record["outcome"] == "agree" for record in loo_records),
            "disagree": sum(record["outcome"] == "disagree" for record in loo_records),
            "tool_refused": sum(
                record["outcome"] == "tool_refused" for record in loo_records
            ),
            "characters": loo_characters,
            "correct_characters": loo_correct_characters,
        },
        "leave_one_out_records": loo_records,
        "leave_one_out_margin": {
            "cells": len(loo_margins),
            "minimum": min(loo_margins) if loo_margins else None,
            "p05": round(float(np.quantile(loo_margins, 0.05)), 6)
            if loo_margins else None,
            "median": round(float(np.median(loo_margins)), 6)
            if loo_margins else None,
        },
        "reader_disagreements": {
            "checked": len(rankings),
            "ranked": len(ranked),
            "refused": len(rankings) - len(ranked),
            "preferred_correct": sum(record["preferred_correct"] for record in ranked),
            "agree": sum(record["outcome"] == "agree" for record in rankings),
            "disagree": sum(record["outcome"] == "disagree" for record in rankings),
            "tool_refused": sum(
                record["outcome"] == "tool_refused" for record in rankings
            ),
        },
        "jackknife_stability": {
            "checked": len(ranked),
            "stable": sum(record["jackknife_stable"] for record in ranked),
            "unstable": sum(not record["jackknife_stable"] for record in ranked),
            "stable_correct": sum(
                record["jackknife_stable"] and record["preferred_correct"]
                for record in ranked
            ),
            "stable_incorrect": sum(
                record["jackknife_stable"] and not record["preferred_correct"]
                for record in ranked
            ),
        },
        "rankings": rankings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate a document-specific fixed-font glyph atlas."
    )
    parser.add_argument("report", type=Path)
    parser.add_argument("crop_manifest", type=Path)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(args.report, args.crop_manifest, args.source_sha256)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    summary = result["reader_disagreements"]
    loo = result["leave_one_out"]
    print(
        f"glyph atlas leave-one-out: {loo['agree']} agree, {loo['disagree']} disagree, "
        f"{loo['tool_refused']} refused; "
        f"ranked {summary['ranked']}/{summary['checked']} disagreements, "
        f"preferred {summary['preferred_correct']} labelled values"
    )


if __name__ == "__main__":
    main()
