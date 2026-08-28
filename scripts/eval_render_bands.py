"""Calibrate the render-check bands against the frozen labelled equation corpus.

    uv run python scripts/eval_render_bands.py [--json OUT]

For each of the 12 source-labelled equations: render its region from the
hash-pinned PDF, score the GROUND-TRUTH LaTeX against that crop (the ceiling),
score any production candidate the frozen baseline marked full_exact (a natural
positive), score deterministic component-shaped corruptions of the truth
(drop a subscript, swap fraction arguments, substitute a symbol, drop a term —
the failure modes render-back exists to catch), and score production candidates
the baseline marked not-exact (natural negatives).

The output is the score distribution per class and the measured separation, so
RENDER_SIMILAR_ABOVE / RENDER_DISSIMILAR_BELOW become data cuts instead of
guesses. Inputs are hash-checked; the report is written to
docs/render-band-calibration.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_LABELS = _ROOT / "tests" / "equation_labels.json"
_RECOVERY = _ROOT / "tests" / "equation_recovery_corpus.json"
_BASELINE = _ROOT / "out" / "reviews" / "equation-components-v1.json"

_DPI = 220
_PADDING_PT = 6.0


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _check_inputs() -> tuple[dict, dict, dict]:
    recovery = json.loads(_RECOVERY.read_text())
    problems = []
    for name, artifact in recovery.get("artifacts", {}).items():
        path = _ROOT / artifact["path"]
        if _sha(path) != artifact.get("sha256"):
            problems.append(f"{name}: {artifact['path']}")
    labels = json.loads(_LABELS.read_text())
    if len(labels) != 12:
        problems.append(f"expected 12 labelled equations, found {len(labels)}")
    if problems:
        sys.exit("input check failed:\n  " + "\n  ".join(problems))
    return recovery, labels, json.loads(_BASELINE.read_text())


def _provenances(recovery: dict) -> dict[str, dict]:
    """Map each pinned provenance by its own document's source filename."""
    out = {}
    for name, artifact in recovery.get("artifacts", {}).items():
        if not name.endswith("_provenance"):
            continue
        path = _ROOT / artifact["path"]
        doc = json.loads(path.read_text())
        source = Path(doc.get("source_path", "")).name
        if not source:
            sys.exit(f"{name}: provenance carries no source_path")
        out[source] = {"blocks": {b["id"]: b for b in doc.get("blocks", [])}}
    return out


def _corruptions(latex: str) -> list[tuple[str, str]]:
    """Deterministic, component-shaped corruptions of correct LaTeX."""
    out: list[tuple[str, str]] = []
    m = re.search(r"_\{[^{}]*\}|_[A-Za-z0-9]", latex)
    if m:
        out.append(("drop-subscript", latex[:m.start()] + latex[m.end():]))
    m = re.search(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", latex)
    if m:
        swapped = latex[:m.start()] + f"\\\\frac{{{m.group(2)}}}{{{m.group(1)}}}" \
            + latex[m.end():]
        out.append(("fraction-swap", swapped))
    for greek, other in ((r"\rho", r"\eta"), (r"\alpha", r"\beta"),
                         (r"\nu", r"\mu"), (r"\gamma", r"\theta")):
        if greek in latex:
            out.append(("symbol-swap", latex.replace(greek, other, 1)))
            break
    if "+" in latex:
        out.append(("term-drop", latex[:latex.rfind("+")].rstrip()))
    return [(name, variant) for name, variant in out if variant != latex]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", type=Path,
                        default=_ROOT / "docs" / "render-band-calibration.json")
    args = parser.parse_args()

    recovery, labels, baseline = _check_inputs()
    provs = _provenances(recovery)
    records_by_key = {
        (r["source"], r["labelled_block_id"]): r for r in baseline.get("records", [])
    }

    from pdf2md.confidence import compare_render
    from pdf2md.render import CropRenderer
    from pdf2md.schema import BBox

    results = []
    by_source: dict[str, list[dict]] = {}
    for label in labels:
        by_source.setdefault(label["source"], []).append(label)

    for source, source_labels in by_source.items():
        prov = provs.get(source)
        if prov is None:
            results.extend({"source": source, "block_id": l["block_id"],
                            "page": l["page"], "status": "no_pinned_artifact"}
                           for l in source_labels)
            continue
        with CropRenderer(_ROOT / source, dpi=_DPI,
                          padding_pts=_PADDING_PT) as renderer:
            for label in source_labels:
                block_id = label["block_id"]
                record = records_by_key.get((source, block_id))
                block = prov["blocks"].get(block_id)
                if record is None or block is None or block.get("bbox") is None:
                    results.append({"source": source, "block_id": block_id,
                                    "page": label["page"], "status": "unresolvable"})
                    continue

                bbox = BBox(**block["bbox"])
                crop_path = (_ROOT / "out" / "render-band-crops"
                             / f"{block_id.strip('#/').replace('/', '_')}_p{label['page']}.png")
                crop_path.parent.mkdir(parents=True, exist_ok=True)
                renderer.crop(label["page"], bbox, crop_path, dpi=_DPI)

                entry = {
                    "source": source, "block_id": block_id, "page": label["page"],
                    "kind": label["kind"],
                    "positive_truth": compare_render(crop_path, label["latex"]),
                }
                candidate = record.get("candidates", {}).get(
                    record.get("selected_channel") or "engine", {})
                cand_latex = candidate.get("latex")
                if cand_latex:
                    entry["positive_production_exact"] = (
                        compare_render(crop_path, cand_latex)
                        if candidate.get("full_exact") else None)
                    entry["negative_production"] = (
                        None if candidate.get("full_exact")
                        else compare_render(crop_path, cand_latex))
                entry["negatives_synthetic"] = [
                    {"kind": name, **compare_render(crop_path, variant)}
                    for name, variant in _corruptions(label["latex"])
                ]
                results.append(entry)

    pos = sorted(r["positive_truth"]["score"] for r in results
                 if isinstance(r.get("positive_truth"), dict)
                 and "score" in r["positive_truth"])
    prod_pos = sorted(r["positive_production_exact"]["score"] for r in results
                      if r.get("positive_production_exact")
                      and "score" in r["positive_production_exact"])
    nat_neg = sorted(r["negative_production"]["score"] for r in results
                     if r.get("negative_production")
                     and "score" in r["negative_production"])
    synth = sorted(n["score"] for r in results
                   for n in r.get("negatives_synthetic", []) if "score" in n)

    def stats(values):
        return {"n": len(values), "min": min(values) if values else None,
                "median": round(statistics.median(values), 3) if values else None,
                "max": max(values) if values else None}

    summary = {
        "positive_truth_ceiling": stats(pos),
        "positive_production_exact": stats(prod_pos),
        "negative_natural_production": stats(nat_neg),
        "negative_synthetic_corruptions": stats(synth),
        "separation": {
            "min_positive_above_max_negative":
                bool(pos and synth and min(pos) > max(synth)),
            "current_bands": {"similar_above": 0.70, "dissimilar_below": 0.45},
        },
    }
    print(json.dumps(summary, indent=1))
    for r in results:
        if "status" in r:
            print(f"{r['source']:22} p{r['page']:>2} SKIPPED ({r['status']})")
            continue
        pt = r.get("positive_truth", {})
        print(f"{r['source']:22} p{r['page']:>2} {r['kind']:>12} "
              f"truth={pt.get('score', '-')!s:>6} "
              f"synth={[n.get('score', n['verdict']) for n in r.get('negatives_synthetic', [])]} "
              f"prod={'-' if r.get('negative_production') is None else r['negative_production']['score']}")

    args.json.write_text(json.dumps({
        "schema_version": 1,
        "method": "render_back_band_calibration",
        "crop_recipe": {"dpi": _DPI, "padding_pts": _PADDING_PT},
        "inputs": {
            "labels_sha256": _sha(_LABELS),
            "recovery_corpus_sha256": _sha(_RECOVERY),
        },
        "summary": summary,
        "results": results,
    }, indent=2))
    print(f"report written: {args.json}")


if __name__ == "__main__":
    main()
