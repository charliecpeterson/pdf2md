"""Measure exact and structural variance across pinned MinerU repeats."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from collections import Counter
from pathlib import Path

from pdf2md.engines.mineru import _translate_middle


ROOT = Path(__file__).parent.parent
DEFAULT_SOURCES = ROOT / "tests" / "mineru_repeat_stability_sources.json"
DEFAULT_CORPUS = ROOT / "tests" / "mineru_repeat_stability_corpus.json"
DEFAULT_REPORT = ROOT / "out" / "reviews" / "mineru-repeat-stability-v1.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bbox_tuple(bbox) -> tuple[float, float, float, float]:
    return (
        min(bbox.x0, bbox.x1),
        min(bbox.y0, bbox.y1),
        max(bbox.x0, bbox.x1),
        max(bbox.y0, bbox.y1),
    )


def _iou(first: tuple[float, ...], second: tuple[float, ...]) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    union = first_area + second_area - intersection
    return intersection / union if union else float(first == second)


def _load_run(root: Path, artifact: dict) -> dict:
    path = root / artifact["path"]
    digest = _sha256(path)
    if digest != artifact["sha256"]:
        raise ValueError(f"MinerU repeat artifact hash mismatch: {artifact['path']}")
    document = json.loads(path.read_text())
    translated = _translate_middle(document, "mineru 3.4.4")
    tables = {table.block_id: table for table in translated.tables}
    records = []
    for block in translated.blocks:
        if block.bbox is None:
            continue
        content = tables[block.id].gfm if block.id in tables else block.text
        records.append({
            "id": block.id,
            "page": block.page,
            "type": block.type.value,
            "bbox": _bbox_tuple(block.bbox),
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        })
    return {
        "id": artifact["id"],
        "path": artifact["path"],
        "sha256": digest,
        "blocks": len(translated.blocks),
        "tables": len(translated.tables),
        "figures": len(translated.figures),
        "by_type": dict(sorted(Counter(block.type.value for block in translated.blocks).items())),
        "table_pages": dict(sorted(Counter(table.page for table in translated.tables).items())),
        "radial_table_gfm_rows": {
            str(page): [
                len(table.gfm.splitlines())
                for table in translated.tables
                if table.page == page
            ]
            for page in (18, 19, 20, 21)
        },
        "records": records,
    }


def _pairwise(reference: dict, candidate: dict) -> dict:
    remaining = set(range(len(candidate["records"])))
    matched = []
    for record in reference["records"]:
        options = [
            index for index in remaining
            if candidate["records"][index]["page"] == record["page"]
            and candidate["records"][index]["type"] == record["type"]
        ]
        if not options:
            continue
        best = max(options, key=lambda index: _iou(
            record["bbox"], candidate["records"][index]["bbox"]
        ))
        overlap = _iou(record["bbox"], candidate["records"][best]["bbox"])
        if overlap < 0.9:
            continue
        remaining.remove(best)
        matched.append((record, candidate["records"][best], overlap))
    matched_by_type = Counter(first["type"] for first, _, _ in matched)
    identical_by_type = Counter(
        first["type"]
        for first, second, _ in matched
        if first["content_sha256"] == second["content_sha256"]
    )
    changed_by_type = {
        block_type: matched_by_type[block_type] - identical_by_type[block_type]
        for block_type in sorted(matched_by_type)
        if matched_by_type[block_type] != identical_by_type[block_type]
    }
    identical_content = sum(identical_by_type.values())
    matched_tables = matched_by_type["table"]
    identical_tables = identical_by_type["table"]
    return {
        "reference": reference["id"],
        "candidate": candidate["id"],
        "exact_json": reference["sha256"] == candidate["sha256"],
        "matched_blocks": len(matched),
        "reference_blocks_with_geometry": len(reference["records"]),
        "candidate_blocks_with_geometry": len(candidate["records"]),
        "block_recall_at_iou_0_9": round(
            len(matched) / len(reference["records"]), 8
        ),
        "block_precision_at_iou_0_9": round(
            len(matched) / len(candidate["records"]), 8
        ),
        "matched_content_identical": identical_content,
        "matched_content_identity_rate": round(identical_content / len(matched), 8),
        "changed_content_by_type": changed_by_type,
        "matched_table_blocks": matched_tables,
        "matched_table_content_identical": identical_tables,
        "matched_table_content_identity_rate": round(
            identical_tables / matched_tables, 8
        ),
        "minimum_matched_iou": round(min(
            (overlap for _, _, overlap in matched), default=0.0
        ), 8),
    }


def _public_run(run: dict) -> dict:
    return {key: value for key, value in run.items() if key != "records"}


def _checked_run(run: dict) -> dict:
    return {
        key: run[key]
        for key in (
            "id",
            "sha256",
            "blocks",
            "tables",
            "figures",
            "by_type",
            "radial_table_gfm_rows",
        )
    }


def _checked_result(report: dict) -> dict:
    return {
        "sources_sha256": report["sources_sha256"],
        "environment": report["environment"],
        "runs": {
            "prior": _checked_run(report["runs"]["prior"]),
            "repeats": [
                _checked_run(run) for run in report["runs"]["repeats"]
            ],
        },
        "repeat_pairs": report["repeat_pairs"],
        "conclusion": report["conclusion"],
    }


def evaluate(root: Path, sources_path: Path) -> dict:
    sources = json.loads(sources_path.read_text())
    if sources.get("schema_version") != 1:
        raise ValueError("unsupported MinerU repeat sources schema_version")
    prior = _load_run(root, sources["prior_run"])
    repeats = [_load_run(root, artifact) for artifact in sources["repeats"]]
    pairs = [
        _pairwise(first, second)
        for first, second in itertools.combinations(repeats, 2)
    ]
    prior_pairs = [_pairwise(prior, repeat) for repeat in repeats]
    unique_hashes = len({repeat["sha256"] for repeat in repeats})
    radial_rows_stable = all(
        len({
            tuple(repeat["radial_table_gfm_rows"][str(page)])
            for repeat in repeats
        }) == 1
        for page in (18, 19, 20, 21)
    )
    return {
        "schema_version": 1,
        "method": "immutable_offline_mineru_repeat_comparison",
        "sources_sha256": _sha256(sources_path),
        "environment": sources["environment"],
        "runs": {
            "prior": _public_run(prior),
            "repeats": [_public_run(repeat) for repeat in repeats],
        },
        "repeat_pairs": pairs,
        "prior_to_repeat_pairs": prior_pairs,
        "conclusion": {
            "unique_repeat_middle_json_hashes": unique_hashes,
            "bitwise_repeatable": unique_hashes == 1,
            "prior_run_reproduced_bitwise": all(pair["exact_json"] for pair in prior_pairs),
            "block_count_range": [
                min(repeat["blocks"] for repeat in repeats),
                max(repeat["blocks"] for repeat in repeats),
            ],
            "table_count_range": [
                min(repeat["tables"] for repeat in repeats),
                max(repeat["tables"] for repeat in repeats),
            ],
            "minimum_pairwise_block_recall": min(
                pair["block_recall_at_iou_0_9"] for pair in pairs
            ),
            "minimum_pairwise_content_identity": min(
                pair["matched_content_identity_rate"] for pair in pairs
            ),
            "minimum_pairwise_table_content_identity": min(
                pair["matched_table_content_identity_rate"] for pair in pairs
            ),
            "radial_row_counts_stable": radial_rows_stable,
            "promotion": "do_not_replace_better_v5_extraction",
            "interpretation": "repeatability measures runtime stability, not extraction correctness; the repeated parse remains one table short of v5",
        },
    }


def check_corpus(root: Path, corpus_path: Path, report: dict) -> bool:
    corpus = json.loads(corpus_path.read_text())
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported MinerU repeat corpus schema_version")
    for name, artifact in corpus["artifacts"].items():
        if _sha256(root / artifact["path"]) != artifact["sha256"]:
            raise ValueError(f"MinerU repeat corpus artifact hash mismatch: {name}")
    return _checked_result(report) == corpus["expected"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = evaluate(args.root, args.sources)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    conclusion = report["conclusion"]
    print(
        f"MinerU repeats: {conclusion['unique_repeat_middle_json_hashes']} unique "
        f"middle JSON hashes; tables {conclusion['table_count_range']}"
    )
    if args.check and not check_corpus(args.root, args.corpus, report):
        raise SystemExit("MinerU repeat corpus differs from expected results")


if __name__ == "__main__":
    main()
