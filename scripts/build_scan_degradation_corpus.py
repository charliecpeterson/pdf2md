"""Build deterministic raster variants of one source-grounded scientific table.

The generated PDF contains one degradation regime per page. Its manifest records
the exact source, operations, tool versions, page images, and final corpus hash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image, __version__ as pillow_version


_ROOT = Path(__file__).parent.parent
_SOURCE = _ROOT / "dolg-ecp.pdf"
_OUTPUT = _ROOT / "output" / "pdf" / "dolg-table-iii-scan-degradation.pdf"
_ABLATION_OUTPUT = _ROOT / "output" / "pdf" / "dolg-table-iii-combined-ablation.pdf"
_CROP = "2050x1900+250+60"
_COMBINED_FACTORS = {
    "resolution": [
        "-filter", "Lanczos", "-resize", "683x633!", "-resize", "2050x1900!",
    ],
    "blur": ["-blur", "0x1.2"],
    "contrast": ["-brightness-contrast", "0x-35"],
    "rotation": [
        "-background", "white", "-rotate", "1.5", "-gravity", "center",
        "-crop", "2050x1900+0+0", "+repage",
    ],
    "jpeg": ["jpeg_roundtrip:40"],
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tool_version(command: list[str]) -> str:
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    return (completed.stdout or completed.stderr).splitlines()[0]


def _variants() -> list[dict]:
    return [
        {"id": "clean_300dpi", "operations": []},
        {
            "id": "resolution_150dpi",
            "operations": [
                "-filter", "Lanczos", "-resize", "1025x950!", "-resize", "2050x1900!",
            ],
        },
        {
            "id": "resolution_100dpi",
            "operations": [
                "-filter", "Lanczos", "-resize", "683x633!", "-resize", "2050x1900!",
            ],
        },
        {"id": "blur_sigma_1_2", "operations": ["-blur", "0x1.2"]},
        {"id": "blur_sigma_2_0", "operations": ["-blur", "0x2.0"]},
        {
            "id": "skew_1_degree",
            "operations": [
                "-background", "white", "-rotate", "1.0", "-gravity", "center",
                "-crop", "2050x1900+0+0", "+repage",
            ],
        },
        {
            "id": "skew_2_degrees",
            "operations": [
                "-background", "white", "-rotate", "2.0", "-gravity", "center",
                "-crop", "2050x1900+0+0", "+repage",
            ],
        },
        {"id": "contrast_minus_35", "operations": ["-brightness-contrast", "0x-35"]},
        {"id": "contrast_minus_55", "operations": ["-brightness-contrast", "0x-55"]},
        {"id": "jpeg_quality_25", "operations": ["jpeg_roundtrip:25"]},
        {"id": "jpeg_quality_10", "operations": ["jpeg_roundtrip:10"]},
        {
            "id": "combined_hard",
            "operations": _combined_operations(tuple(_COMBINED_FACTORS)),
        },
    ]


def _combined_operations(factors: tuple[str, ...]) -> list[str]:
    return [operation for factor in factors for operation in _COMBINED_FACTORS[factor]]


def _combined_ablation_variants() -> list[dict]:
    factors = tuple(_COMBINED_FACTORS)
    variants = [{
        "id": "clean_control",
        "operations": [],
        "factors": [],
        "role": "control",
    }, {
        "id": "combined_all",
        "operations": _combined_operations(factors),
        "factors": list(factors),
        "role": "full_combination",
    }]
    for removed in factors:
        active = tuple(factor for factor in factors if factor != removed)
        variants.append({
            "id": f"combined_without_{removed}",
            "operations": _combined_operations(active),
            "factors": list(active),
            "removed_factor": removed,
            "role": "leave_one_out",
        })
    return variants


def _render_variant(magick: str, base: Path, destination: Path, operations: list[str]) -> None:
    image_operations = [item for item in operations if not item.startswith("jpeg_roundtrip:")]
    jpeg_quality = next(
        (item.partition(":")[2] for item in operations if item.startswith("jpeg_roundtrip:")),
        None,
    )
    if jpeg_quality is None:
        subprocess.run(
            [magick, str(base), *image_operations, "-strip", str(destination)],
            check=True,
        )
        return

    jpeg_path = destination.with_suffix(".jpg")
    subprocess.run(
        [
            magick, str(base), *image_operations, "-quality", jpeg_quality,
            "-strip", str(jpeg_path),
        ],
        check=True,
    )
    subprocess.run(
        [magick, str(jpeg_path), "-strip", str(destination)], check=True
    )
    jpeg_path.unlink()


def build(
    source: Path,
    output: Path,
    work_dir: Path,
    *,
    variants: list[dict] | None = None,
    method: str = "controlled_table_scan_degradation",
) -> dict:
    pdftoppm = shutil.which("pdftoppm")
    magick = shutil.which("magick")
    if pdftoppm is None or magick is None:
        missing = [name for name, path in (("pdftoppm", pdftoppm), ("magick", magick)) if path is None]
        raise RuntimeError(f"required tool unavailable: {', '.join(missing)}")

    work_dir.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    page_prefix = work_dir / "source-page"
    subprocess.run([
        pdftoppm, "-f", "7", "-l", "7", "-singlefile", "-png", "-r", "300",
        str(source), str(page_prefix),
    ], check=True)
    rendered_page = page_prefix.with_suffix(".png")
    base = work_dir / "table-base.png"
    subprocess.run(
        [magick, str(rendered_page), "-crop", _CROP, "+repage", str(base)], check=True
    )

    variant_records = []
    page_images = []
    if variants is None:
        variants = _variants()
    for page, variant in enumerate(variants, start=1):
        image_path = work_dir / f"{page:02d}-{variant['id']}.png"
        _render_variant(magick, base, image_path, variant["operations"])
        with Image.open(image_path) as image:
            page_images.append(image.convert("RGB"))
            size = list(image.size)
        variant_records.append({
            "page": page,
            "image": image_path.name,
            "image_sha256": _sha256(image_path),
            "pixel_size": size,
            **variant,
        })

    first, *rest = page_images
    first.save(
        output,
        "PDF",
        save_all=True,
        append_images=rest,
        resolution=300.0,
        title=output.stem,
        creationDate="",
        modDate="",
    )
    for image in page_images:
        image.close()

    manifest = {
        "schema_version": 1,
        "method": method,
        "source": source.name,
        "source_sha256": _sha256(source),
        "source_page": 7,
        "source_crop_300dpi": _CROP,
        "corpus_pdf": output.name,
        "corpus_sha256": _sha256(output),
        "tools": {
            "pdftoppm": _tool_version([pdftoppm, "-v"]),
            "imagemagick": _tool_version([magick, "-version"]),
            "pillow": pillow_version,
        },
        "variants": variant_records,
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build controlled raster degradations of Dolg Table III."
    )
    parser.add_argument("--source", type=Path, default=_SOURCE)
    parser.add_argument(
        "--suite",
        choices=("baseline", "combined-ablation"),
        default="baseline",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()

    ablation = args.suite == "combined-ablation"
    output = args.output or (_ABLATION_OUTPUT if ablation else _OUTPUT)
    work_dir = args.work_dir or _ROOT / "tmp" / "pdfs" / (
        "combined-ablation" if ablation else "scan-degradation"
    )
    manifest = build(
        args.source,
        output,
        work_dir,
        variants=_combined_ablation_variants() if ablation else None,
        method=(
            "combined_degradation_leave_one_factor_out"
            if ablation
            else "controlled_table_scan_degradation"
        ),
    )
    print(
        f"scan corpus: {len(manifest['variants'])} pages, "
        f"sha256 {manifest['corpus_sha256']}"
    )


if __name__ == "__main__":
    main()
