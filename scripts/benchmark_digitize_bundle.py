"""Replay chart digitization from an existing bundle without re-running the parser.

    uv run python scripts/benchmark_digitize_bundle.py BUNDLE [--limit N]

The benchmark reads figure geometry and crop paths from provenance.json. It does not
write to the bundle, so repeated timing runs exercise the same inputs.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import json
import logging
from pathlib import Path
import time

from pdf2md.config import Config
from pdf2md.logging import Progress
from pdf2md.schema import BBox, FigureRef
from pdf2md.visual import _digitize_figures


def _figures(bundle: Path, limit: int | None) -> tuple[list[FigureRef], dict[str, str]]:
    raw = json.loads((bundle / "provenance.json").read_text())
    figures = []
    accepted = {}
    for item in raw["figures"][:limit]:
        bbox = BBox(**item["bbox"]) if item.get("bbox") else None
        figures.append(
            FigureRef(
                block_id=item["block_id"],
                page=item["page"],
                bbox=bbox,
                asset_path=item.get("asset_path", ""),
            )
        )
        if item.get("digitization") is not None:
            accepted[item["block_id"]] = json.dumps(
                item["digitization"], sort_keys=True
            )
    return figures, accepted


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark chart digitization against an existing pdf2md bundle."
    )
    parser.add_argument("bundle", type=Path, help="Completed version directory containing provenance.json.")
    parser.add_argument("--limit", type=int, help="Process only the first N figures.")
    args = parser.parse_args()

    bundle = args.bundle.expanduser().resolve()
    source = bundle.parent / "source.pdf"
    if not source.is_file():
        parser.error(f"source PDF not found: {source}")
    if not (bundle / "provenance.json").is_file():
        parser.error(f"provenance not found: {bundle / 'provenance.json'}")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")

    figures, accepted_before = _figures(bundle, args.limit)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    started = time.perf_counter()
    _digitize_figures(
        figures,
        source,
        Config(),
        None,
        bundle,
        progress=Progress(logging.getLogger("pdf2md.benchmark_digitize")),
    )
    seconds = time.perf_counter() - started
    methods = Counter(
        figure.digitization.method
        for figure in figures
        if figure.digitization is not None
    )
    accepted_after = {
        figure.block_id: json.dumps(asdict(figure.digitization), sort_keys=True)
        for figure in figures
        if figure.digitization is not None
    }
    changed = sorted(
        block_id
        for block_id in accepted_before.keys() & accepted_after.keys()
        if accepted_before[block_id] != accepted_after[block_id]
    )
    print(
        json.dumps(
            {
                "figures": len(figures),
                "seconds": round(seconds, 3),
                "figures_per_second": round(len(figures) / seconds, 3) if seconds else None,
                "recovered": sum(methods.values()),
                "methods": dict(sorted(methods.items())),
                "accepted_before": len(accepted_before),
                "same_accepted_ids": accepted_before.keys() == accepted_after.keys(),
                "same_digitizations": not changed and accepted_before.keys() == accepted_after.keys(),
                "changed_digitizations": changed,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
