"""Prepare a detector-masked human review; see docs/scan-evaluation-protocol.md.

This development harness joins frozen scan reports to the existing page sampler
and label scorer. It neither runs OCR nor supplies accuracy labels.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from _missing_content_review import write_review
from _scan_omissions import POLICY
from probe_missing_content import bundle_hashes, digest, sample_pages, score, sha256, write_json

SEED = 20260923
PER_STRATUM = 2
BUDGET = 4


def load_document(directory: Path, expected: dict) -> tuple[dict, list[dict]]:
    manifest_path, report_path = directory / "manifest.json", directory / "report.json"
    manifest = json.loads(manifest_path.read_text())
    report = json.loads(report_path.read_text())
    if (manifest.get("schema_version") != 1 or report.get("schema_version") != 1
            or report["manifest_sha256"] != sha256(manifest_path)
            or manifest["policy"] != POLICY):
        raise ValueError("reader report, manifest, or policy differs")
    if manifest["source_sha256"] != expected["source_sha256"]:
        raise ValueError("unexpected source identity")
    source, bundle = Path(manifest["source"]), Path(manifest["bundle"])
    if (sha256(source) != expected["source_sha256"]
            or bundle_hashes(bundle) != manifest["bundle_files"]):
        raise ValueError("source or bundle changed after reading")
    pages = report["pages"]
    if sorted(p["page"] for p in pages) != list(range(1, expected["pages"] + 1)):
        raise ValueError("reader report must cover each source page exactly once")
    baseline = json.loads((bundle / "review.json").read_text())["items"]
    frame = []
    for page in pages:
        number = page["page"]
        for field, suffix in (("source_image_sha256", "png"), ("reader_tsv_sha256", "tsv")):
            if field in page and sha256(directory / f"page-{number:03d}.{suffix}") != page[field]:
                raise ValueError("reader evidence changed")
        reasons = [item["reason"] for item in baseline
                   if item["page"] == number and item["disposition"] == "action_required"]
        counts = Counter(line["status"] for line in page["lines"])
        frame.append({
            "id": f"{expected['source_sha256']}:p{number}",
            "document_id": expected["source_sha256"], "page": number,
            "source": str(source), "bundle": str(bundle),
            "baseline_flagged": bool(reasons), "baseline_reasons": reasons,
            "probe_flagged": counts["reader_disagreement"] > 0,
            "reader_status": page["status"], "line_status": dict(counts),
        })
    pin = {
        "name": expected["name"], "source_sha256": expected["source_sha256"],
        "reader_directory": str(directory), "manifest_sha256": sha256(manifest_path),
        "report_sha256": sha256(report_path), "reader": manifest["reader"],
        "implementation_sha256": manifest["implementation_sha256"],
    }
    return pin, frame


def prepare(sources_path: Path, directories: list[Path], output: Path) -> dict:
    sources = json.loads(sources_path.read_text())
    protocol = Path(__file__).resolve().parents[1] / sources["protocol"]
    if sha256(protocol) != sources["protocol_sha256"]:
        raise ValueError("frozen protocol changed")
    expected = {doc["source_sha256"]: doc for doc in sources["sources"]}
    if not expected or len(expected) != len(sources["sources"]):
        raise ValueError("sources must be nonempty and unique")
    if len(directories) != len(expected):
        raise ValueError("one reader report per frozen source is required")
    output = output.resolve()
    if output.exists():
        raise ValueError("use a new output directory")
    pins, pages, seen = [], [], set()
    for directory in sorted(p.resolve() for p in directories):
        manifest = json.loads((directory / "manifest.json").read_text())
        identity = manifest["source_sha256"]
        if identity not in expected or identity in seen:
            raise ValueError("duplicate or unexpected reader source")
        if (output.is_relative_to(Path(manifest["bundle"]).resolve().parent)
                or output.is_relative_to(directory)):
            raise ValueError("review output must be outside input directories")
        pin, frame = load_document(directory, expected[identity])
        pins.append(pin)
        pages.extend(frame)
        seen.add(identity)
    if len({digest(p["reader"]) for p in pins}) != 1 or len({
        digest(p["implementation_sha256"]) for p in pins
    }) != 1:
        raise ValueError("reader or implementation differs across documents")
    pages.sort(key=lambda p: (p["document_id"], p["page"]))
    sample, strata = sample_pages(pages, PER_STRATUM, SEED)
    identity = {
        "sources_sha256": sha256(sources_path), "protocol_sha256": sha256(protocol),
        "seed": SEED, "sample_per_stratum": PER_STRATUM, "review_budget": BUDGET,
        "documents": pins,
        "review_implementation_sha256": {
            name: sha256(Path(__file__).parent / name) for name in
            ("prepare_scan_review.py", "_missing_content_review.py", "probe_missing_content.py")
        },
    }
    report = {
        "schema_version": 1, "experiment_id": digest(identity), **identity,
        "pages": pages, "sample": sample, "strata": strata,
        "summary": {
            "documents": len(pins), "pages": len(pages), "sampled_pages": len(sample),
            "baseline_flagged_pages": sum(p["baseline_flagged"] for p in pages),
            "probe_flagged_pages": sum(p["probe_flagged"] for p in pages),
            "reader_status": dict(Counter(p["reader_status"] for p in pages)),
            "labels_completed": 0, "accuracy": None,
        },
        "limits": "Detector-masked, not fully blinded: original output warnings remain visible. "
        "No human labels yet. Unflagged and reader-agreement pages are not verified clean.",
    }
    labels = {
        "schema_version": 1, "experiment_id": report["experiment_id"],
        "labels": [{"id": p["id"], "omission": "unreviewed", "reviewer": "", "note": ""}
                   for p in sample],
    }
    output.mkdir(parents=True)
    write_review(report, output, show_detector_hints=False)
    # Recheck after rendering so the review cannot silently pin changing inputs.
    for pin in pins:
        current, _ = load_document(Path(pin["reader_directory"]), expected[pin["source_sha256"]])
        if current != pin:
            raise ValueError("reader inputs changed during preparation")
    write_json(output / "report.json", report)
    write_json(output / "labels.json", labels)
    write_json(output / "scores-pending.json", score(report, labels, BUDGET))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", type=Path)
    parser.add_argument("readers", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = prepare(args.sources, args.readers, args.output)
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
