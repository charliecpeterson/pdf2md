"""Build deterministic degradation variants across source-labelled table families."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image, __version__ as pillow_version


ROOT = Path(__file__).parent.parent
DEFAULT_SOURCES = ROOT / "tests" / "multifamily_degradation_sources.json"
DEFAULT_OUTPUT = ROOT / "output" / "pdf" / "multifamily-table-degradation.pdf"
DEFAULT_WORK = ROOT / "tmp" / "pdfs" / "multifamily-degradation"

VARIANTS = (
    {"id": "clean", "factors": [], "operations": []},
    {"id": "dpi_150", "factors": ["resolution"], "operations": ["scale:0.5"]},
    {"id": "dpi_100", "factors": ["resolution"], "operations": ["scale:0.333333"]},
    {"id": "blur_1_2", "factors": ["blur"], "operations": ["blur:1.2"]},
    {"id": "skew_1_5", "factors": ["skew"], "operations": ["rotate:1.5"]},
    {"id": "contrast_minus_35", "factors": ["contrast"], "operations": ["contrast:-35"]},
    {"id": "adaptive_binarization", "factors": ["binarization"], "operations": ["lat:25x25+5%"]},
    {"id": "jpeg_25", "factors": ["jpeg"], "operations": ["jpeg:25"]},
    {
        "id": "dpi_100_contrast",
        "factors": ["resolution", "contrast"],
        "operations": ["scale:0.333333", "contrast:-35"],
        "role": "interaction",
    },
    {
        "id": "skew_blur",
        "factors": ["skew", "blur"],
        "operations": ["rotate:1.5", "blur:1.2"],
        "role": "interaction",
    },
    {
        "id": "combined_hard",
        "factors": ["resolution", "blur", "contrast", "skew", "jpeg"],
        "operations": [
            "scale:0.333333",
            "blur:1.2",
            "contrast:-35",
            "rotate:1.5",
            "jpeg:40",
        ],
        "role": "interaction",
    },
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tool_version(command: list[str]) -> str:
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    return (completed.stdout or completed.stderr).splitlines()[0]


def _magick_operations(
    operations: list[str], width: int, height: int
) -> tuple[list[str], str | None]:
    arguments = []
    jpeg_quality = None
    for operation in operations:
        name, _, raw_value = operation.partition(":")
        if name == "scale":
            fraction = float(raw_value)
            reduced = (
                max(1, round(width * fraction)),
                max(1, round(height * fraction)),
            )
            arguments.extend([
                "-filter", "Lanczos", "-resize", f"{reduced[0]}x{reduced[1]}!",
                "-resize", f"{width}x{height}!",
            ])
        elif name == "blur":
            arguments.extend(["-blur", f"0x{raw_value}"])
        elif name == "contrast":
            arguments.extend(["-brightness-contrast", f"0x{raw_value}"])
        elif name == "rotate":
            arguments.extend([
                "-background", "white", "-rotate", raw_value,
                "-gravity", "center", "-crop", f"{width}x{height}+0+0", "+repage",
            ])
        elif name == "lat":
            arguments.extend([
                "-colorspace", "Gray", "-lat", raw_value, "-negate",
            ])
        elif name == "jpeg":
            jpeg_quality = raw_value
        else:
            raise ValueError(f"unsupported degradation operation: {operation}")
    return arguments, jpeg_quality


def _render_variant(
    magick: str,
    source: Path,
    destination: Path,
    operations: list[str],
) -> list[str]:
    with Image.open(source) as image:
        width, height = image.size
    arguments, jpeg_quality = _magick_operations(operations, width, height)
    if jpeg_quality is None:
        subprocess.run(
            [magick, str(source), *arguments, "-strip", str(destination)],
            check=True,
        )
    else:
        jpeg = destination.with_suffix(".jpg")
        subprocess.run(
            [
                magick, str(source), *arguments, "-quality", jpeg_quality,
                "-strip", str(jpeg),
            ],
            check=True,
        )
        subprocess.run([magick, str(jpeg), "-strip", str(destination)], check=True)
        jpeg.unlink()
    return arguments + (["jpeg_roundtrip", jpeg_quality] if jpeg_quality else [])


def _select(records: list[dict], requested: list[str] | None, label: str) -> list[dict]:
    if requested is None:
        return records
    available = {record["id"] for record in records}
    unknown = sorted(set(requested) - available)
    if unknown:
        raise ValueError(f"unknown {label}: {', '.join(unknown)}")
    wanted = set(requested)
    return [record for record in records if record["id"] in wanted]


def _manifest_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def build(
    sources_path: Path,
    output: Path,
    work_dir: Path,
    *,
    family_ids: list[str] | None = None,
    variant_ids: list[str] | None = None,
) -> dict:
    sources = json.loads(sources_path.read_text())
    if sources.get("schema_version") != 1:
        raise ValueError("unsupported multifamily degradation source schema_version")
    magick = shutil.which("magick")
    if magick is None:
        raise RuntimeError("required tool unavailable: magick")

    for artifact in sources.get("artifacts", {}).values():
        artifact_path = ROOT / artifact["path"]
        if _sha256(artifact_path) != artifact["sha256"]:
            raise ValueError(f"source artifact hash mismatch: {artifact['path']}")

    families = _select(sources["families"], family_ids, "table family")
    variants = _select(list(VARIANTS), variant_ids, "degradation variant")
    if not families or not variants:
        raise ValueError("at least one table family and degradation variant are required")

    output.parent.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    pages = []
    images = []
    for family in families:
        source_crop = ROOT / family["source_crop"]
        if _sha256(source_crop) != family["source_crop_sha256"]:
            raise ValueError(f"source crop hash mismatch: {family['id']}")
        for variant in variants:
            page = len(pages) + 1
            image_path = work_dir / f"{page:03d}-{family['id']}-{variant['id']}.png"
            resolved_operations = _render_variant(
                magick, source_crop, image_path, variant["operations"]
            )
            with Image.open(image_path) as image:
                images.append(image.convert("RGB"))
                pixel_size = list(image.size)
            pages.append({
                "page": page,
                "family": family["id"],
                "variant": variant["id"],
                "factors": variant["factors"],
                "role": variant.get("role", "isolated" if variant["factors"] else "control"),
                "requested_operations": variant["operations"],
                "resolved_operations": resolved_operations,
                "image": image_path.name,
                "image_sha256": _sha256(image_path),
                "pixel_size": pixel_size,
            })

    first, *rest = images
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
    for image in images:
        image.close()

    manifest = {
        "schema_version": 1,
        "method": "controlled_multifamily_table_degradation",
        "sources": _manifest_path(sources_path),
        "sources_sha256": _sha256(sources_path),
        "corpus_pdf": output.name,
        "corpus_sha256": _sha256(output),
        "families": [
            {
                key: family[key]
                for key in (
                    "id", "source_crop", "source_crop_sha256", "typography", "features"
                )
            }
            for family in families
        ],
        "variants": [
            {
                "id": variant["id"],
                "factors": variant["factors"],
                "role": variant.get("role", "isolated" if variant["factors"] else "control"),
            }
            for variant in variants
        ],
        "pages": pages,
        "tools": {
            "imagemagick": _tool_version([magick, "-version"]),
            "pillow": pillow_version,
        },
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build degradation variants for multiple table families."
    )
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK)
    parser.add_argument(
        "--family",
        action="append",
        dest="family_ids",
        help="include one table family; repeat to select several",
    )
    parser.add_argument(
        "--variant",
        action="append",
        dest="variant_ids",
        help="include one degradation variant; repeat to select several",
    )
    args = parser.parse_args()

    manifest = build(
        args.sources,
        args.output,
        args.work_dir,
        family_ids=args.family_ids,
        variant_ids=args.variant_ids,
    )
    print(
        f"multifamily degradation corpus: {len(manifest['pages'])} pages, "
        f"sha256 {manifest['corpus_sha256']}"
    )


if __name__ == "__main__":
    main()
