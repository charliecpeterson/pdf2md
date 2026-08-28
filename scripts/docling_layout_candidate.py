"""Run one commit-pinned Docling layout model and retain its native exports.

This experiment keeps OCR, table recognition, formula recognition, and export
settings fixed so only the standard pipeline's layout detector changes.
"""

from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import re
from typing import Any


_DEPENDENCIES = (
    "docling",
    "docling-core",
    "docling-ibm-models",
    "docling-parse",
    "rapidocr",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_candidate(path: Path, candidate_id: str) -> dict[str, Any]:
    registry = json.loads(path.read_text())
    if registry.get("schema_version") != 1:
        raise ValueError(f"{path}: unsupported schema_version")
    candidates = registry.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError(f"{path}: candidates must be a list")
    matches = [candidate for candidate in candidates if candidate.get("id") == candidate_id]
    if len(matches) != 1:
        raise ValueError(f"{path}: expected one candidate named {candidate_id}")
    candidate = matches[0]
    revision = candidate.get("revision")
    weight_sha256 = candidate.get("weight_sha256")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError(f"{path}: {candidate_id} revision is not a commit hash")
    if not isinstance(weight_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", weight_sha256
    ):
        raise ValueError(f"{path}: {candidate_id} weight_sha256 is invalid")
    return candidate


def _versions() -> dict[str, str]:
    installed: dict[str, str] = {}
    for package in _DEPENDENCIES:
        try:
            installed[package] = version(package)
        except PackageNotFoundError:
            installed[package] = "not installed"
    return installed


def _verify_weights(candidate: dict[str, Any], snapshot: Path) -> dict[str, Any]:
    weights = snapshot / candidate["model_path"] / candidate["weight_file"]
    if not weights.is_file():
        raise FileNotFoundError(f"model weight not found: {weights}")
    weight_bytes = weights.stat().st_size
    weight_sha256 = _sha256(weights)
    if weight_bytes != candidate["weight_bytes"]:
        raise ValueError(
            f"{weights}: {weight_bytes} bytes != pinned {candidate['weight_bytes']}"
        )
    if weight_sha256 != candidate["weight_sha256"]:
        raise ValueError(
            f"{weights}: sha256 {weight_sha256} != pinned {candidate['weight_sha256']}"
        )
    return {
        "snapshot_path": str(snapshot),
        "weight_path": str(weights.relative_to(snapshot)),
        "weight_bytes": weight_bytes,
        "weight_sha256": weight_sha256,
    }


def convert(source: Path, output: Path, candidate: dict[str, Any]) -> None:
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.layout_model_specs import LayoutModelConfig
    from docling.datamodel.pipeline_options import (
        LayoutOptions,
        PdfPipelineOptions,
        RapidOcrOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.models.utils.hf_model_download import download_hf_model
    from docling_core.types.doc import ImageRefMode

    model_spec = LayoutModelConfig(
        name=candidate["name"],
        repo_id=candidate["repo_id"],
        revision=candidate["revision"],
        model_path=candidate["model_path"],
    )
    options = PdfPipelineOptions()
    options.layout_options = LayoutOptions(model_spec=model_spec)
    options.do_ocr = True
    options.ocr_options = RapidOcrOptions(
        rapidocr_params={"Global.log_level": "WARNING"}
    )
    options.do_table_structure = True
    options.do_formula_enrichment = True
    options.do_picture_classification = False
    options.do_picture_description = False
    options.do_chart_extraction = False
    options.generate_page_images = True
    options.generate_picture_images = True
    options.images_scale = 2
    options.accelerator_options = AcceleratorOptions(
        num_threads=4,
        device=AcceleratorDevice.AUTO,
    )

    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
    )
    conversion = converter.convert(source, raises_on_error=True)

    output.mkdir(parents=True, exist_ok=True)
    stem = source.stem
    conversion.document.save_as_json(
        filename=output / f"{stem}.json",
        image_mode=ImageRefMode.REFERENCED,
    )
    conversion.document.save_as_markdown(
        filename=output / f"{stem}.md",
        image_mode=ImageRefMode.REFERENCED,
    )

    snapshot = download_hf_model(
        repo_id=candidate["repo_id"],
        revision=candidate["revision"],
    )
    provenance = {
        "schema_version": 1,
        "candidate": candidate,
        "dependencies": _versions(),
        "configuration": {
            "pipeline": "standard",
            "ocr": "rapidocr",
            "table_structure": True,
            "formula_enrichment": True,
            "picture_classification": False,
            "picture_description": False,
            "chart_extraction": False,
            "image_export_mode": "referenced",
            "images_scale": 2,
            "device": "auto",
            "num_threads": 4,
        },
        "source": {
            "path": str(source.resolve()),
            "bytes": source.stat().st_size,
            "sha256": _sha256(source),
        },
        "model_artifact": _verify_weights(candidate, snapshot),
    }
    (output / "candidate.json").write_text(json.dumps(provenance, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one pinned Docling layout candidate.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    candidate = _load_candidate(args.candidates.resolve(), args.candidate)
    convert(args.source.resolve(), args.output.resolve(), candidate)


if __name__ == "__main__":
    main()
