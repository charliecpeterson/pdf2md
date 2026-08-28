"""Build the human and machine review queues from explicit coverage dispositions.

The queue keeps likely defects ahead of valid image dependence and uses the same
records for review.md, review.json, the manifest, and profile counts.
"""

from __future__ import annotations

import json
from pathlib import Path

from pdf2md.schema import Document

_DISPOSITION_ORDER = {"action_required": 0, "source_dependent": 1, "informational": 2}
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "none": 3}
_IMPACT_ORDER = {"high": 0, "medium": 1, "low": 2}


def build_review_queue(doc: Document) -> dict:
    blocks = {block.id: block for block in doc.blocks}
    figures = {figure.block_id: figure for figure in doc.figures}
    items = []
    for flag in doc.coverage.flags if doc.coverage else []:
        block = blocks.get(flag.block_id)
        figure = figures.get(flag.block_id)
        asset = block.extra.get("crop_path") if block else None
        if not asset and figure:
            asset = figure.asset_path or None
        items.append({
            "disposition": flag.disposition,
            "severity": flag.severity,
            "content_impact": flag.content_impact,
            "content_type": block.type.value if block else "unknown",
            "block_id": flag.block_id,
            "page": flag.page,
            "reason": flag.reason,
            "asset": asset,
            "source_page": f"../source.pdf#page={flag.page}",
        })
    items.sort(key=lambda item: (
        _DISPOSITION_ORDER.get(item["disposition"], 99),
        _SEVERITY_ORDER.get(item["severity"], 99),
        _IMPACT_ORDER.get(item["content_impact"], 99),
        item["page"],
        item["block_id"],
    ))
    counts = {
        disposition: sum(item["disposition"] == disposition for item in items)
        for disposition in _DISPOSITION_ORDER
    }
    return {"schema_version": 1, "counts": counts, "items": items}


def write_review_files(version_dir: Path, queue: dict) -> tuple[Path, Path]:
    json_path = version_dir / "review.json"
    json_path.write_text(json.dumps(queue, indent=2))

    counts = queue["counts"]
    lines = [
        "# Review queue",
        "",
        f"Action required: {counts['action_required']}. Source-dependent: "
        f"{counts['source_dependent']}. Informational: {counts['informational']}.",
        "",
        "Likely errors and missing representations appear before valid image dependence.",
        "",
    ]
    labels = {
        "action_required": "Action required",
        "source_dependent": "Source-dependent",
        "informational": "Informational",
    }
    for disposition in _DISPOSITION_ORDER:
        lines += [f"## {labels[disposition]}", ""]
        records = [item for item in queue["items"] if item["disposition"] == disposition]
        if not records:
            lines += ["None.", ""]
            continue
        lines += ["| Severity | Impact | Page | Type | Reason | Evidence |", "|---|---|---:|---|---|---|"]
        for item in records:
            evidence = f"[source]({item['source_page']})"
            if item["asset"]:
                evidence += f" · [asset]({item['asset']})"
            lines.append(
                f"| {item['severity']} | {item['content_impact']} | {item['page']} | "
                f"{item['content_type']} | {item['reason']} | {evidence} |"
            )
        lines.append("")

    markdown_path = version_dir / "review.md"
    markdown_path.write_text("\n".join(lines))
    return markdown_path, json_path
