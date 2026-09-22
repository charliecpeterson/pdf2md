"""Read-only probe for content outside represented regions in completed bundles.

Freeze source/conversion hashes before scanning, sample flagged and unflagged pages,
and score source-reviewed omission labels. Experiment record:
docs/missing-content-pilot.md. This is not a production accuracy gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from contextlib import closing
from importlib.metadata import version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pypdfium2 as pdfium
from _missing_content import POLICY, filter_running_furniture, inspect_page
from _missing_content_review import write_review

from pdf2md.enrich import GlyphIndex
from pdf2md.schema import BBox


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def bundle_hashes(bundle: Path) -> dict[str, str]:
    return {str(path.relative_to(bundle)): sha256(path)
            for path in sorted(bundle.rglob("*")) if path.is_file()}


def implementation_hashes() -> dict[str, str]:
    names = ("probe_missing_content.py", "_missing_content.py", "_missing_content_review.py")
    files = {f"scripts/{name}": Path(__file__).parent / name for name in names}
    for name in ("enrich.py", "scripts.py", "schema.py"):
        relative = f"src/pdf2md/{name}"
        files[relative] = Path(__file__).parents[1] / relative
    return {name: sha256(path) for name, path in files.items()}


def runtime_versions() -> dict[str, str]:
    return {"python": sys.version, "pypdfium2": version("pypdfium2"), "pillow": version("pillow")}


def freeze(bundles: list[Path], output: Path, exposure: str, family: str) -> dict:
    if output.exists():
        raise ValueError(f"refusing to overwrite manifest: {output}")
    documents = []
    seen = set()
    for bundle in sorted(path.resolve() for path in bundles):
        provenance_path = bundle / "provenance.json"
        provenance = json.loads(provenance_path.read_text())
        source = bundle.parent / "source.pdf"
        source_hash = sha256(source)
        if source_hash != provenance["source_sha256"]:
            raise ValueError(f"source hash mismatch: {bundle}")
        if source_hash in seen:
            raise ValueError("one conversion per source is required in a sampling frame")
        seen.add(source_hash)
        review = bundle / "review.json"
        documents.append({
            "id": source_hash,
            "source": str(source),
            "source_sha256": source_hash,
            "bundle": str(bundle),
            "provenance_sha256": sha256(provenance_path),
            "review_sha256": sha256(review),
            "bundle_files": bundle_hashes(bundle),
            "family": family,
            "exposure": exposure,
        })
    manifest = {
        "schema_version": 1,
        "purpose": "page-level missing-content screening pilot",
        "claims": "No accuracy claim until source review; previously used sources are development data.",
        "policy": POLICY,
        "implementation_sha256": implementation_hashes(),
        "runtime_versions": runtime_versions(),
        "documents": documents,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, manifest)
    return manifest


def sample_pages(pages: list[dict], per_stratum: int, seed: int) -> tuple[list[dict], list[dict]]:
    if per_stratum < 1:
        raise ValueError("sample-per-stratum must be positive")
    groups = defaultdict(list)
    for page in pages:
        stratum = "flagged" if page["baseline_flagged"] or page["probe_flagged"] else "unflagged"
        groups[page["document_id"], stratum].append(page)
    selected, strata = [], []
    for (document, stratum), pool in sorted(groups.items()):
        pool = sorted(pool, key=lambda p: p["page"])
        rng = random.Random(f"{seed}:{document}:{stratum}")
        count = min(per_stratum, len(pool))
        chosen = rng.sample(pool, count)
        strata.append({"document_id": document, "stratum": stratum,
                       "population": len(pool), "sampled": count})
        for page in chosen:
            selected.append({**page, "stratum": stratum,
                             "inclusion_probability": count / len(pool)})
    return sorted(selected, key=lambda p: (p["document_id"], p["page"])), strata


def page_blocks(blocks: list[dict]) -> dict[int, list[dict]]:
    """Use full regions for emitted text; a primary crop cannot cover other pages."""
    by_page = defaultdict(list)
    for block in blocks:
        spans = block.get("source_spans") or [{"page": block["page"], "bbox": block["bbox"]}]
        if block["coverage_status"] != "emitted" or block.get("extra", {}).get("crop_path"):
            spans = [{"page": block["page"], "bbox": block["bbox"]}]
        for span in spans:
            by_page[span["page"]].append({**block, "page": span["page"], "bbox": span["bbox"]})
    return by_page


def scan_document(document: dict) -> list[dict]:
    source = Path(document["source"])
    bundle = Path(document["bundle"])
    if bundle_hashes(bundle) != document["bundle_files"]:
        raise ValueError(f"pinned bundle changed: {bundle}")
    for path, expected in ((source, document["source_sha256"]),
                           (bundle / "provenance.json", document["provenance_sha256"]),
                           (bundle / "review.json", document["review_sha256"])):
        if sha256(path) != expected:
            raise ValueError(f"pinned input changed: {path}")
    provenance = json.loads((bundle / "provenance.json").read_text())
    review = json.loads((bundle / "review.json").read_text())
    blocks = page_blocks(provenance["blocks"])
    flags = defaultdict(list)
    for item in review["items"]:
        if item["disposition"] == "action_required":
            flags[item["page"]].append(item["reason"])
    pages = []
    config = provenance.get("provenance", {}).get("run_inputs", {}).get("effective_config", {})
    force_ocr = config.get("force_ocr", False)
    with GlyphIndex(source, force_ocr=force_ocr) as glyphs, closing(pdfium.PdfDocument(source)) as pdf:
        if len(pdf) != provenance["page_count"]:
            raise ValueError(f"page count mismatch: {bundle}")
        if any(not isinstance(page, int) or not 1 <= page <= len(pdf) for page in blocks):
            raise ValueError(f"block page outside source: {bundle}")
        for number in range(1, len(pdf) + 1):
            page = pdf[number - 1]
            try:
                if page.get_rotation() != 0:
                    # Glyph and raster orientations need an explicit transform.
                    # Refuse this initial probe rather than manufacture a clean result.
                    result = {"glyph_check": "unsupported_page_rotation", "candidates": []}
                else:
                    box = page.get_bbox()
                    pc = glyphs.page_chars(number)
                    chars = pc.region_chars(BBox(*box)) if pc is not None else None
                    bitmap = page.render(scale=POLICY["render_dpi"] / 72)
                    try:
                        image = bitmap.to_pil().convert("L")
                        histogram = image.histogram()
                        ink = sum(histogram[:POLICY["dark_pixel_threshold"]]) / (image.width * image.height)
                        image.close()
                    finally:
                        bitmap.close()
                    result = inspect_page(box, chars, blocks[number], ink)
                pages.append({
                    "id": f"{document['id']}:p{number}",
                    "document_id": document["id"],
                    "page": number,
                    "source": str(source),
                    "bundle": str(bundle),
                    "baseline_flagged": bool(flags[number]),
                    "baseline_reasons": flags[number],
                    "probe_flagged": any(c["actionable"] for c in result["candidates"]),
                    **result,
                })
            finally:
                page.close()
    filter_running_furniture(pages)
    return pages


def scan(manifest_path: Path, output: Path, per_stratum: int, seed: int) -> dict:
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != 1 or manifest["policy"] != POLICY:
        raise ValueError("manifest schema or probe policy differs; freeze a new experiment")
    if manifest.get("implementation_sha256") != implementation_hashes():
        raise ValueError("probe implementation changed; freeze a new experiment")
    if manifest.get("runtime_versions") != runtime_versions():
        raise ValueError("probe runtime changed; freeze a new experiment")
    if not manifest["documents"]:
        raise ValueError("manifest contains no documents")
    if len({d["id"] for d in manifest["documents"]}) != len(manifest["documents"]):
        raise ValueError("duplicate documents in manifest")
    if per_stratum < 1:
        raise ValueError("sample-per-stratum must be positive")
    if output.exists():
        raise ValueError(f"use a new output directory: {output}")
    pages = [page for document in manifest["documents"] for page in scan_document(document)]
    selected, strata = sample_pages(pages, per_stratum, seed)
    identity = {"manifest_sha256": sha256(manifest_path), "policy": POLICY,
                "seed": seed, "per_stratum": per_stratum,
                "pages": pages, "selected_ids": [page["id"] for page in selected]}
    report = {
        "schema_version": 1,
        "experiment_id": digest(identity),
        "manifest": manifest,
        "manifest_sha256": identity["manifest_sha256"],
        "policy": POLICY,
        "seed": seed,
        "sample_per_stratum": per_stratum,
        "summary": {
            "documents": len(manifest["documents"]),
            "pages": len(pages),
            "baseline_flagged_pages": sum(p["baseline_flagged"] for p in pages),
            "probe_flagged_pages": sum(p["probe_flagged"] for p in pages),
            "probe_flagged_before_furniture": sum(p["probe_flagged_before_furniture"] for p in pages),
            "probable_furniture_candidates": sum(c["kind"] == "probable_running_furniture"
                                                  for p in pages for c in p["candidates"]),
            "newly_flagged_pages": sum(p["probe_flagged"] and not p["baseline_flagged"] for p in pages),
            "glyph_check_status": dict(Counter(p["glyph_check"] for p in pages)),
            "sampled_pages": len(selected),
            "labelled_pages": 0,
            "accuracy": None,
        },
        "limitations": [
            "Geometry coverage cannot establish text accuracy inside a represented region.",
            "Textless pages with existing blocks are not checked for partial omissions.",
            "Margin and short runs remain evidence but do not trigger review.",
            "Page screening is not localization precision or calibrated confidence.",
            "Continued paragraphs may be present in output but lack geometry on this page.",
        ],
        "strata": strata,
        "pages": pages,
        "sample": selected,
    }
    output.mkdir(parents=True)
    write_json(output / "report.json", report)
    write_json(output / "labels.json", {
        "schema_version": 1, "experiment_id": report["experiment_id"],
        "labels": [{"id": p["id"], "omission": "unreviewed", "reviewer": "", "note": ""}
                   for p in selected],
    })
    write_review(report, output)
    return report


def score(report: dict, labels: dict, budget: int) -> dict:
    if labels.get("schema_version") != 1 or labels.get("experiment_id") != report["experiment_id"]:
        raise ValueError("labels belong to a different experiment")
    sample = {p["id"]: p for p in report["sample"]}
    records = labels["labels"]
    if len({r["id"] for r in records}) != len(records):
        raise ValueError("duplicate labels")
    if {r["id"] for r in records} != set(sample):
        raise ValueError("labels must cover exactly the frozen sample, including unreviewed entries")
    if budget < 1:
        raise ValueError("budget must be positive")
    known = {}
    for record in records:
        if record["omission"] not in {"yes", "no", "uncertain", "unreviewed"}:
            raise ValueError("unknown omission label")
        if record["omission"] in {"yes", "no"}:
            if not record["reviewer"].strip() or not record["note"].strip():
                raise ValueError("reviewed pages require a reviewer and source evidence note")
            known[record["id"]] = record["omission"] == "yes"
    complete = len(known) == len(sample)
    strategies = {}
    for name, field in (("baseline", "baseline_flagged"), ("probe", "probe_flagged")):
        ordered = sorted(sample.values(), key=lambda p: (not p[field],
                         digest([report["seed"], p["id"]])))[:budget]
        judged = [p for p in ordered if p["id"] in known]
        strategies[name] = {
            "budget": len(ordered), "reviewed": len(judged),
            "omission_pages_found": (sum(known[p["id"]] for p in judged)
                                     if len(judged) == len(ordered) else None),
            "complete": len(judged) == len(ordered),
            "selected_ids": [p["id"] for p in ordered],
        }
    unflagged = [p for p in sample.values() if not p["baseline_flagged"]]
    denominator = sum(1 / p["inclusion_probability"] for p in unflagged)
    rate = (sum(known[p["id"]] / p["inclusion_probability"] for p in unflagged)
            / denominator if complete and denominator else None)
    population = [p for p in report.get("pages", []) if not p["baseline_flagged"]]
    population_yes = sum(known.get(p["id"]) is True for p in population)
    population_no = sum(known.get(p["id"]) is False for p in population)
    bounds = ({"lower": population_yes / len(population),
               "upper": (len(population) - population_no) / len(population),
               "population_pages": len(population),
               "labelled_pages": population_yes + population_no,
               "method": "finite-corpus worst-case bounds; unlabelled pages may all be clean or omitted",
               "assumption": "definite source-review labels are correct; not a confidence interval"}
              if population else None)
    screening = {}
    for name, field in (("baseline", "baseline_flagged"), ("probe", "probe_flagged")):
        judged = [p for p in sample.values() if p["id"] in known]
        screening[name] = {
            "true_positive_pages": sum(p[field] and known[p["id"]] for p in judged),
            "false_positive_pages": sum(p[field] and not known[p["id"]] for p in judged),
            "false_negative_pages": sum(not p[field] and known[p["id"]] for p in judged),
            "true_negative_pages": sum(not p[field] and not known[p["id"]] for p in judged),
        }
    return {
        "schema_version": 1, "experiment_id": report["experiment_id"],
        "sampled_pages": len(sample), "reviewed_pages": len(known),
        "complete": complete,
        "omission_pages_observed": sum(known.values()),
        "estimated_baseline_unflagged_omission_page_rate": rate,
        "uncertainty_interval": None,
        "baseline_unflagged_finite_corpus_bounds": bounds,
        "reviewed_sample_screening": screening,
        "interpretation": (
            "Descriptive pilot only; no independence assumption or calibrated-confidence claim. "
            "Equal-budget comparison is within the frozen review sample, not the whole corpus. "
            "A warning on an omission-bearing page does not establish correct localization."
        ),
        "fixed_budget": strategies,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freezing = commands.add_parser("freeze", help="pin existing bundle inputs before scanning")
    freezing.add_argument("bundles", nargs="+", type=Path)
    freezing.add_argument("--output", type=Path, required=True)
    freezing.add_argument("--exposure", choices=["development", "uninspected_output"], required=True)
    freezing.add_argument("--family", required=True)
    scanning = commands.add_parser("scan", help="write a new report and source review sheet")
    scanning.add_argument("manifest", type=Path)
    scanning.add_argument("--output", type=Path, required=True)
    scanning.add_argument("--sample-per-stratum", type=int, default=3)
    scanning.add_argument("--seed", type=int, default=20260922)
    scoring = commands.add_parser("score", help="score completed source review, without inventing labels")
    scoring.add_argument("report", type=Path)
    scoring.add_argument("labels", type=Path)
    scoring.add_argument("--budget", type=int, default=10)
    scoring.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "freeze":
        result = freeze(args.bundles, args.output, args.exposure, args.family)
        print(f"frozen {len(result['documents'])} documents: {args.output}")
    elif args.command == "scan":
        result = scan(args.manifest, args.output, args.sample_per_stratum, args.seed)
        print(json.dumps(result["summary"], indent=2))
    else:
        if args.output.exists():
            parser.error("refusing to overwrite score output")
        result = score(json.loads(args.report.read_text()), json.loads(args.labels.read_text()), args.budget)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, result)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
