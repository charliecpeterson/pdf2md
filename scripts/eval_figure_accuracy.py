"""Score figure filtering, panel containment, and caption association."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from pdf2md.schema import BBox, Block, BlockType, FigureLabels, FigureRef
from pdf2md.visual import clean_figure_structure


ROOT = Path(__file__).parent.parent
DEFAULT_CORPUS = ROOT / "tests" / "figure_accuracy_corpus.json"
DEFAULT_REPORT = ROOT / "out" / "reviews" / "figure-accuracy-v1.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _block(record: dict) -> Block:
    return Block(
        record["id"],
        BlockType(record["type"]),
        record["text"],
        record["page"],
        BBox(**record["bbox"]) if record.get("bbox") else None,
    )


def _figure(record: dict) -> FigureRef:
    return FigureRef(
        record["block_id"],
        record["page"],
        BBox(**record["bbox"]) if record.get("bbox") else None,
        caption=record.get("caption"),
        caption_bbox=(
            BBox(**record["caption_bbox"]) if record.get("caption_bbox") else None
        ),
        labels=FigureLabels(**record["labels"]) if record.get("labels") else None,
    )


def _bbox_values(bbox: BBox | None) -> list[float] | None:
    return [bbox.x0, bbox.y0, bbox.x1, bbox.y1] if bbox is not None else None


def _bounds(values: BBox | list[float]) -> tuple[float, float, float, float]:
    if isinstance(values, BBox):
        x0, y0, x1, y1 = values.x0, values.y0, values.x1, values.y1
    else:
        x0, y0, x1, y1 = values
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def _overlaps(first: BBox | None, second: list[float]) -> bool:
    if first is None:
        return False
    ax0, ay0, ax1, ay1 = _bounds(first)
    bx0, by0, bx1, by1 = _bounds(second)
    return min(ax1, bx1) > max(ax0, bx0) and min(ay1, by1) > max(ay0, by0)


def evaluate(root: Path, corpus_path: Path) -> dict:
    corpus = json.loads(corpus_path.read_text())
    reports = []
    for labelled in corpus["documents"]:
        source = root / labelled["source"]["path"]
        provenance_path = root / labelled["conversion"]["provenance_path"]
        if _sha256(source) != labelled["source"]["sha256"]:
            raise ValueError(f"figure source hash mismatch: {labelled['id']}")
        if _sha256(provenance_path) != labelled["conversion"]["provenance_sha256"]:
            raise ValueError(f"figure provenance hash mismatch: {labelled['id']}")
        provenance = json.loads(provenance_path.read_text())
        blocks = [_block(record) for record in provenance["blocks"]]
        figures = [_figure(record) for record in provenance["figures"]]
        initial_ids = {figure.block_id for figure in figures}
        counts = clean_figure_structure(blocks, figures)
        by_id = {figure.block_id: figure for figure in figures}
        scientific = []
        for expected in labelled["scientific_figures"]:
            figure = by_id.get(expected["block_id"])
            caption_prefix = expected.get("caption_prefix")
            scientific.append({
                "block_id": expected["block_id"],
                "retained": figure is not None,
                "bbox_exact": (
                    figure is not None and _bbox_values(figure.bbox) == expected["bbox"]
                ),
                "caption_associated": (
                    None
                    if caption_prefix is None
                    else figure is not None
                    and (figure.caption or "").startswith(caption_prefix)
                ),
                "features": expected.get("features", []),
            })
        retained_ids = set(by_id)
        labelled_ids = {
            expected["block_id"] for expected in labelled["scientific_figures"]
        } | set(labelled["furniture_ids"]) | {
            block_id
            for group in labelled.get("fragment_groups", [])
            for block_id in group["subsumed_ids"]
        }
        fragment_results = []
        for group in labelled.get("fragment_groups", []):
            remaining = sorted(retained_ids.intersection(group["subsumed_ids"]))
            fragment_results.append({
                "logical_figure": group["logical_figure"],
                "remaining_ids": remaining,
                "resolved": not remaining,
            })
        furniture_exclusions = []
        for expected in labelled.get("furniture_exclusions", []):
            overlaps = sorted(
                figure.block_id
                for figure in figures
                if figure.page == expected["page"]
                and _overlaps(figure.bbox, expected["bbox"])
            )
            furniture_exclusions.append({
                "label": expected["label"],
                "page": expected["page"],
                "overlapping_figure_ids": overlaps,
                "excluded": not overlaps,
            })
        reports.append({
            "id": labelled["id"],
            "publisher": labelled["publisher"],
            "initial_figures": len(initial_ids),
            "unlabelled_initial_ids": sorted(initial_ids - labelled_ids),
            "labelled_dispositions_complete": initial_ids == labelled_ids,
            **counts,
            "retained_ids": sorted(retained_ids),
            "removed_ids": sorted(initial_ids - retained_ids),
            "removals_exact": sorted(initial_ids - retained_ids)
            == sorted(labelled["furniture_ids"] + [
                block_id
                for group in labelled.get("fragment_groups", [])
                for block_id in group["subsumed_ids"]
            ]),
            "fragments": fragment_results,
            "furniture_exclusions": furniture_exclusions,
            "scientific": scientific,
        })

    scientific = [item for report in reports for item in report["scientific"]]
    feature_coverage = Counter(
        feature for item in scientific for feature in item["features"]
    )
    captioned = [
        item for item in scientific if item["caption_associated"] is not None
    ]
    exclusions = [
        item for report in reports for item in report["furniture_exclusions"]
    ]
    summary = {
        "documents": len(reports),
        "publisher_families": len({report["publisher"] for report in reports}),
        "publishers": sorted({report["publisher"] for report in reports}),
        "labelled_scientific_figures": len(scientific),
        "documents_with_complete_dispositions": sum(
            report["labelled_dispositions_complete"] for report in reports
        ),
        "scientific_figures_retained": sum(item["retained"] for item in scientific),
        "exact_content_bboxes": sum(item["bbox_exact"] for item in scientific),
        "labelled_caption_associations": len(captioned),
        "correct_caption_associations": sum(
            item["caption_associated"] for item in captioned
        ),
        "feature_coverage": dict(sorted(feature_coverage.items())),
        "journal_furniture_labelled": sum(
            len(document["furniture_ids"]) for document in corpus["documents"]
        ),
        "journal_furniture_removed": sum(
            report["furniture_removed"] for report in reports
        ),
        "labelled_furniture_exclusions": len(exclusions),
        "furniture_regions_excluded": sum(item["excluded"] for item in exclusions),
        "fragmented_figures_before": sum(
            len(document.get("fragment_groups", [])) for document in corpus["documents"]
        ),
        "fragmented_figures_after": sum(
            not group["resolved"]
            for report in reports
            for group in report["fragments"]
        ),
    }
    return {
        "schema_version": 1,
        "corpus_sha256": _sha256(corpus_path),
        "documents": reports,
        "summary": summary,
    }


def check_corpus(corpus_path: Path, report: dict) -> bool:
    expected = json.loads(corpus_path.read_text())["expected"]
    if report["summary"] != expected["summary"]:
        raise ValueError("figure accuracy summary differs from the frozen corpus")
    for actual, frozen in zip(report["documents"], expected["documents"], strict=True):
        checked = {
            key: actual[key]
            for key in (
                "id",
                "publisher",
                "unlabelled_initial_ids",
                "labelled_dispositions_complete",
                "furniture_removed",
                "panels_merged",
                "fragments_merged",
                "graphic_components_included",
                "panel_headings_absorbed",
                "retained_ids",
                "removed_ids",
                "removals_exact",
                "fragments",
                "furniture_exclusions",
            )
        }
        if checked != frozen:
            raise ValueError(f"figure outcomes differ from the frozen corpus: {actual['id']}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = evaluate(ROOT, args.corpus)
    if args.check:
        check_corpus(args.corpus, report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    summary = report["summary"]
    print(
        f"figure accuracy: {summary['scientific_figures_retained']}/"
        f"{summary['labelled_scientific_figures']} retained, "
        f"{summary['journal_furniture_removed']} furniture removed, "
        f"{summary['fragmented_figures_after']} fragmented"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
