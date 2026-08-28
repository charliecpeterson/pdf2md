"""Stress every column-geometry layout under deterministic image degradation.

Source-checked header lanes define geometry only. Each transformed row is exact,
wrong, or refused; a lower refusal count never compensates for a wrong mapping.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from collections import Counter
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

import eval_column_geometry_methods as methods


ROOT = Path(__file__).parent.parent
DEFAULT_SOURCES = ROOT / "tests" / "column_geometry_methods_sources.json"
DEFAULT_CORPUS = ROOT / "tests" / "column_geometry_degradation_corpus.json"
DEFAULT_OUTPUT = ROOT / "out" / "reviews" / "column-geometry-degradation-v1"
VARIANTS = (
    "clean",
    "dpi_150",
    "blur_1_2",
    "contrast_65",
    "jpeg_25",
    "adaptive_binarization",
    "combined",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jpeg_roundtrip(image: Image.Image, quality: int) -> Image.Image:
    encoded = BytesIO()
    image.save(encoded, format="JPEG", quality=quality, optimize=False, progressive=False)
    encoded.seek(0)
    with Image.open(encoded) as decoded:
        return decoded.convert("L")


def _downsample(image: Image.Image, scale: float) -> Image.Image:
    reduced = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    return reduced.resize(image.size, Image.Resampling.LANCZOS)


def _adaptive_binarize(image: Image.Image) -> Image.Image:
    gray = np.asarray(image.convert("L"), dtype=np.int16)
    local_mean = np.asarray(image.convert("L").filter(ImageFilter.BoxBlur(15)))
    binary = np.where(gray < local_mean.astype(np.int16) - 10, 0, 255).astype(np.uint8)
    return Image.fromarray(binary, mode="L")


def transform(image: Image.Image, variant: str) -> Image.Image:
    gray = image.convert("L")
    if variant == "clean":
        return gray.copy()
    if variant == "dpi_150":
        return _downsample(gray, 0.5)
    if variant == "blur_1_2":
        return gray.filter(ImageFilter.GaussianBlur(1.2))
    if variant == "contrast_65":
        return ImageEnhance.Contrast(gray).enhance(0.65)
    if variant == "jpeg_25":
        return _jpeg_roundtrip(gray, 25)
    if variant == "adaptive_binarization":
        return _adaptive_binarize(gray)
    if variant == "combined":
        transformed = _downsample(gray, 0.5)
        transformed = transformed.filter(ImageFilter.GaussianBlur(1.2))
        transformed = ImageEnhance.Contrast(transformed).enhance(0.65)
        return _jpeg_roundtrip(transformed, 40)
    raise ValueError(f"unsupported column geometry degradation variant: {variant}")


def _score_lanes(
    image: Image.Image,
    panel: dict,
    row_bands: list[list[int]],
    candidate_lanes: list[list[int]] | list[tuple[int, int]],
    *,
    typed: bool = False,
) -> Counter:
    dark = np.asarray(image.convert("L")) < 200
    counts = Counter()
    for row_band in row_bands:
        runs = methods._merged_runs(
            dark, row_band, panel["panel_bound"], len(panel["reference_lanes"])
        )
        expected_lanes = set()
        candidate_counts = [0] * len(candidate_lanes)
        wrong = False
        for start, stop in runs:
            center = (start + stop) / 2
            expected = methods._lane_index(center, panel["reference_lanes"])
            actual = methods._lane_index(center, candidate_lanes)
            if expected is None or actual != expected:
                wrong = True
            if expected is not None:
                expected_lanes.add(expected)
            if actual is not None:
                candidate_counts[actual] += 1
        if wrong:
            counts["disagree"] += 1
        elif len(expected_lanes) != len(panel["reference_lanes"]):
            counts["tool_refused"] += 1
        elif typed and not all(
            count == 1 if kind == "numeric" else count >= 1
            for count, kind in zip(candidate_counts, panel["column_types"])
        ):
            counts["tool_refused"] += 1
        else:
            counts["agree"] += 1
    return counts


def _row_outcomes(
    image: Image.Image,
    panel: dict,
    row_bands: list[list[int]],
) -> tuple[dict[str, Counter], dict, str | None]:
    candidate_lanes, evidence, refusal = methods.repeated_row_lanes(
        image,
        row_bands,
        panel["panel_bound"],
        len(panel["reference_lanes"]),
    )
    header_exact, header_refused = methods._lane_rows(
        image, row_bands, panel["reference_lanes"]
    )
    header = Counter(agree=header_exact, tool_refused=header_refused)
    if candidate_lanes is None:
        refused = Counter(tool_refused=len(row_bands))
        return {
            "header_fixed": header,
            "consensus": refused,
            "typed_consensus": refused.copy(),
        }, evidence, refusal
    consensus = _score_lanes(image, panel, row_bands, candidate_lanes)
    typed_consensus = _score_lanes(
        image, panel, row_bands, candidate_lanes, typed=True
    )
    return {
        "header_fixed": header,
        "consensus": consensus,
        "typed_consensus": typed_consensus,
    }, evidence, refusal


def _row_bands(panel: dict, alignment: dict[str, dict]) -> list[list[int]]:
    if panel["row_bands"] == "alignment_report":
        return alignment[panel["id"]]["projection"]["bands"]
    return panel["row_bands"]


def _checked_result(report: dict) -> dict:
    return {
        key: report[key]
        for key in (
            "sources_sha256",
            "panels",
            "variants",
            "layout_cases",
            "rows_checked",
            "methods",
            "by_variant",
            "by_panel",
            "cases",
        )
    }


def evaluate(root: Path, sources_path: Path, output_dir: Path) -> dict:
    sources = json.loads(sources_path.read_text())
    if sources.get("schema_version") != 1:
        raise ValueError("unsupported column geometry sources schema_version")
    artifacts, _ = methods._load_artifacts(root, sources)
    alignment = {
        panel["id"]: panel
        for panel in artifacts["alignment_report"]["panels_report"]
    }
    image_dir = output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    method_names = ("header_fixed", "consensus", "typed_consensus")
    total = {method: Counter() for method in method_names}
    variant_counts = {
        variant: {method: Counter() for method in method_names}
        for variant in VARIANTS
    }
    panel_counts = {
        panel["id"]: {method: Counter() for method in method_names}
        for panel in sources["panels"]
    }
    cases = []
    for panel in sources["panels"]:
        source_path = root / panel["source_crop"]
        if _sha256(source_path) != panel["source_crop_sha256"]:
            raise ValueError(f"column degradation crop hash mismatch: {panel['id']}")
        rows = _row_bands(panel, alignment)
        with Image.open(source_path) as source_image:
            source_image.load()
            for variant in VARIANTS:
                image = transform(source_image, variant)
                image_path = image_dir / f"{panel['id']}-{variant}.png"
                image.save(image_path, format="PNG", optimize=False)
                outcomes, evidence, refusal = _row_outcomes(image, panel, rows)
                for method, counts in outcomes.items():
                    if sum(counts.values()) != len(rows):
                        raise ValueError(
                            f"column degradation row accounting failed: {panel['id']}"
                        )
                    total[method].update(counts)
                    variant_counts[variant][method].update(counts)
                    panel_counts[panel["id"]][method].update(counts)
                cases.append({
                    "panel": panel["id"],
                    "variant": variant,
                    "rows": len(rows),
                    "methods": {
                        method: {
                            "agree": counts["agree"],
                            "disagree": counts["disagree"],
                            "tool_refused": counts["tool_refused"],
                        }
                        for method, counts in outcomes.items()
                    },
                    "image": image_path.relative_to(output_dir).as_posix(),
                    "image_sha256": _sha256(image_path),
                    "locator_refusal": refusal,
                    "lanes": evidence.get("lanes"),
                })

    return {
        "schema_version": 1,
        "method": "column_geometry_layout_degradation_gate",
        "contract": {
            "reference": "source-checked header lanes fixed before degradation",
            "locator": "source pixels, fixed row bands, and structural column count only",
            "outcomes": ["agree", "disagree", "tool_refused"],
            "priority": "zero wrong mappings before refusal reduction",
        },
        "runtime": {
            "pillow": importlib.metadata.version("Pillow"),
            "numpy": importlib.metadata.version("numpy"),
        },
        "sources_sha256": _sha256(sources_path),
        "panels": len(sources["panels"]),
        "variants": len(VARIANTS),
        "layout_cases": len(cases),
        "rows_checked": sum(total["consensus"].values()),
        "methods": {
            method: {
                "agree": counts["agree"],
                "disagree": counts["disagree"],
                "tool_refused": counts["tool_refused"],
            }
            for method, counts in total.items()
        },
        "by_variant": {
            variant: {
                method: {
                    "rows": sum(counts.values()),
                    "agree": counts["agree"],
                    "disagree": counts["disagree"],
                    "tool_refused": counts["tool_refused"],
                }
                for method, counts in methods_by_name.items()
            }
            for variant, methods_by_name in variant_counts.items()
        },
        "by_panel": {
            panel: {
                method: {
                    "rows": sum(counts.values()),
                    "agree": counts["agree"],
                    "disagree": counts["disagree"],
                    "tool_refused": counts["tool_refused"],
                }
                for method, counts in methods_by_name.items()
            }
            for panel, methods_by_name in panel_counts.items()
        },
        "cases": cases,
    }


def check_corpus(root: Path, corpus_path: Path, report: dict) -> bool:
    corpus = json.loads(corpus_path.read_text())
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported column geometry degradation corpus schema_version")
    for name, artifact in corpus["artifacts"].items():
        if _sha256(root / artifact["path"]) != artifact["sha256"]:
            raise ValueError(f"column degradation artifact hash mismatch: {name}")
    return _checked_result(report) == corpus["expected"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    report = evaluate(args.root, args.sources, args.output)
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    consensus = report["methods"]["consensus"]
    print(
        f"column degradation: consensus {consensus['agree']}/"
        f"{report['rows_checked']} exact, {consensus['disagree']} wrong, "
        f"{consensus['tool_refused']} refused"
    )
    if args.check and not check_corpus(args.root, args.corpus, report):
        raise SystemExit("column degradation corpus differs from expected results")


if __name__ == "__main__":
    main()
