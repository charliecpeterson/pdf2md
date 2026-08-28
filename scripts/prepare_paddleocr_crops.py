"""Write source-linked labelled cell crops for auxiliary-reader evaluation.

The default geometry comes from the Tesseract row and column alignment used by
pdf2md's independent table reader. A source-box manifest can instead supply
human-verified pixel geometry that does not depend on either OCR reader.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from PIL import Image

from eval_numeric_tables import (
    _candidate_tables,
    _latest_version,
    _table_crops,
    _table_pages,
)
from pdf2md.table_verify import (
    _align_numeric_lines,
    _aligned_tesseract_lines,
    _column_centers,
    _is_property_row,
    _looks_numeric,
    _numeric_read,
    _numeric_source_rows,
    _table_layout,
    _word_lines,
    typed_value,
)
from pdf2md.tables import RepeatedPanelLayout

_LABELS = Path(__file__).parent.parent / "tests" / "numeric_table_labels.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reader_disagreement_labels(labels: dict, report: dict) -> dict:
    wanted = {
        (
            record["source_sha256"],
            record["block_id"],
            record["row"],
            record["column"],
        )
        for record in report.get("records", [])
        if record.get("actual") is not None
        and record.get("reference_actual") is not None
        and record.get("readers_agree") is False
    }
    documents = []
    for document in labels.get("documents", []):
        source_sha256 = document["source_sha256"]
        cells = [
            cell
            for cell in document.get("cells", [])
            if (
                source_sha256,
                cell["block_id"],
                cell["row"],
                cell["column"],
            )
            in wanted
        ]
        if cells:
            documents.append({**document, "cells": cells})
    return {**labels, "documents": documents}


def _limit_labels(labels: dict, maximum: int) -> dict:
    buckets = defaultdict(list)
    for document in labels.get("documents", []):
        source_sha256 = document["source_sha256"]
        for cell in document.get("cells", []):
            buckets[source_sha256, cell["block_id"]].append(cell)
    selected = set()
    while len(selected) < maximum:
        added = False
        for (source_sha256, block_id), cells in buckets.items():
            if cells:
                cell = cells.pop(0)
                selected.add((source_sha256, block_id, cell["row"], cell["column"]))
                added = True
                if len(selected) == maximum:
                    break
        if not added:
            break
    documents = []
    for document in labels.get("documents", []):
        source_sha256 = document["source_sha256"]
        cells = [
            cell for cell in document.get("cells", [])
            if (source_sha256, cell["block_id"], cell["row"], cell["column"])
            in selected
        ]
        if cells:
            documents.append({**document, "cells": cells})
    return {**labels, "documents": documents}


def _version_dir(out_dir: Path, document: dict) -> Path | None:
    version = document.get("version")
    if version is None:
        return _latest_version(out_dir, document["source_sha256"])
    path = out_dir / document["source_sha256"][:16] / version
    return path if (path / "provenance.json").is_file() else None


def _exact_numeric_cell_bounds(
    row: list[str],
    line: list[dict[str, object]],
    column: int,
    image_width: int,
) -> tuple[int, int] | None:
    if column >= len(row) or typed_value(row[column])[2] != "numeric":
        return None
    target = _numeric_read(row[column])
    matches = [
        word for word in line
        if typed_value(str(word["text"]))[2] == "numeric"
        and _numeric_read(str(word["text"])) == target
    ]
    if len(matches) != 1:
        return None
    word = matches[0]
    left = int(word["left"]) - 3
    right = int(word["left"]) + int(word["width"]) + 3
    return max(0, left), min(image_width, right)


def _crop_aligned_lines(
    rows: list[list[str]],
    tsv: str,
    layout: RepeatedPanelLayout,
    wanted_rows: set[int],
) -> list[tuple[int, list[dict[str, object]]]]:
    aligned = _aligned_tesseract_lines(rows, tsv, layout)
    if wanted_rows.issubset(row for row, _ in aligned):
        return aligned
    numeric_rows = sorted(set(_numeric_source_rows(rows)) | wanted_rows)
    candidates = [
        line for line in _word_lines(tsv)
        if any(_looks_numeric(str(word["text"])) for word in line)
    ]
    return _align_numeric_lines(rows, numeric_rows, candidates)


def _column_bounds(
    centers: list[tuple[int, float]],
    layout: RepeatedPanelLayout,
    column: int,
    image_width: int,
) -> tuple[int, int] | None:
    panel_index = max(
        (index for index, start in enumerate(layout.starts) if start <= column),
        default=0,
    )
    panel_start = layout.starts[panel_index]
    panel_end = panel_start + layout.panel_width(panel_index)
    panel_centers = [
        (source_column, center)
        for source_column, center in centers
        if panel_start <= source_column < panel_end
    ]
    center_index = next(
        (index for index, item in enumerate(panel_centers) if item[0] == column),
        None,
    )
    if center_index is None or len(panel_centers) < 2:
        return None
    center = panel_centers[center_index][1]
    if center_index:
        left = (panel_centers[center_index - 1][1] + center) / 2
    else:
        left = center - (panel_centers[1][1] - center) / 2
    if center_index + 1 < len(panel_centers):
        right = (center + panel_centers[center_index + 1][1]) / 2
    else:
        right = center + (center - panel_centers[center_index - 1][1]) / 2
    return max(0, int(left) - 5), min(image_width, int(right) + 5)


def _text_label_cell_bounds(
    row: list[str],
    line: list[dict[str, object]],
    column: int,
    image_width: int,
) -> tuple[int, int] | None:
    if column < 1 or not row or not any(character.isalpha() for character in row[0]):
        return None
    expected = "".join(character.lower() for character in row[0] if character.isalnum())
    candidates = []
    for end in range(1, len(line) + 1):
        observed = "".join(
            character.lower()
            for word in line[:end]
            for character in str(word["text"])
            if character.isalnum()
        )
        candidates.append((SequenceMatcher(None, expected, observed).ratio(), end))
    score, label_end = max(candidates)
    if score < 0.5:
        return None

    remaining = [
        word for word in line[label_end:]
        if any(character.isdigit() for character in str(word["text"]))
    ]
    if not remaining:
        return None
    heights = sorted(int(word["height"]) for word in remaining)
    join_gap = heights[len(heights) // 2]
    groups: list[list[dict[str, object]]] = []
    for word in remaining:
        if groups:
            previous = groups[-1][-1]
            gap = int(word["left"]) - (int(previous["left"]) + int(previous["width"]))
        else:
            gap = join_gap + 1
        if groups and gap <= join_gap:
            groups[-1].append(word)
        else:
            groups.append([word])
    source_columns = [
        source_column
        for source_column, value in enumerate(row[1:], start=1)
        if value.strip()
    ]
    if len(groups) != len(source_columns) or column not in source_columns:
        return None
    group = groups[source_columns.index(column)]
    left = min(int(word["left"]) for word in group) - 3
    right = max(int(word["left"]) + int(word["width"]) for word in group) + 3
    return max(0, left), min(image_width, right)


def _numeric_word_y_bounds(
    line: list[dict[str, object]],
    x_bounds: tuple[int, int],
    image_height: int,
) -> tuple[int, int] | None:
    words = [
        word
        for word in line
        if any(character.isdigit() for character in str(word["text"]))
        and x_bounds[0]
        <= int(word["left"]) + int(word["width"]) / 2
        <= x_bounds[1]
    ]
    if not words:
        return None
    top = min(int(word["top"]) for word in words) - 1
    bottom = max(int(word["top"]) + int(word["height"]) for word in words) + 1
    return max(0, top), min(image_height, bottom)


def write_crops(
    out_dir: Path,
    labels: dict,
    output_dir: Path,
    executable: str,
    scale: int,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    crops = []
    refusals = []
    for document in labels.get("documents", []):
        source_sha256 = document["source_sha256"]
        version_dir = _version_dir(out_dir, document)
        tables = _candidate_tables(version_dir) if version_dir else {}
        table_crops = _table_crops(version_dir) if version_dir else {}
        pages = _table_pages(version_dir) if version_dir else {}
        cells_by_block = defaultdict(list)
        for cell in document.get("cells", []):
            cells_by_block[cell["block_id"]].append(cell)
        page_layouts = {}
        for completed, (block_id, cells) in enumerate(cells_by_block.items(), start=1):
            block_written = 0
            rows = tables.get(block_id)
            source_crop = table_crops.get(block_id)
            page = pages.get(block_id)
            layout = _table_layout(rows, page_layouts.get(page)) if rows else None
            if layout is not None and page is not None:
                page_layouts[page] = layout
            refusal = None
            aligned = {}
            centers = None
            if rows is None:
                refusal = "source_table_missing"
            elif source_crop is None or not source_crop.is_file():
                refusal = "source_crop_missing"
            elif layout is None:
                refusal = "grid_layout_unavailable"
            else:
                try:
                    tesseract = subprocess.run(
                        [
                            executable, str(source_crop), "stdout", "--psm", "6",
                            "-l", "eng", "tsv",
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=120,
                    )
                except subprocess.TimeoutExpired:
                    refusal = "tesseract_timeout"
                else:
                    if tesseract.returncode:
                        refusal = f"tesseract_exit_{tesseract.returncode}"
                    else:
                        aligned_pairs = _crop_aligned_lines(
                            rows,
                            tesseract.stdout,
                            layout,
                            {cell["row"] for cell in cells},
                        )
                        aligned = dict(aligned_pairs)
                        center_lines = [
                            line for row_index, line in aligned_pairs
                            if not _is_property_row(rows[row_index])
                        ] or [line for _, line in aligned_pairs]
                        centers = _column_centers(center_lines, layout)
            if refusal:
                for cell in cells:
                    refusals.append({
                        **cell,
                        "source_sha256": source_sha256,
                        "reason": refusal,
                    })
                continue

            source_crop_sha256 = _sha256(source_crop)
            with Image.open(source_crop) as source_image:
                for cell in cells:
                    row = cell["row"]
                    column = cell["column"]
                    line = aligned.get(row)
                    x_bounds = None
                    if line is not None:
                        x_bounds = _exact_numeric_cell_bounds(
                            rows[row], line, column, source_image.width
                        )
                    if line is not None and x_bounds is None:
                        x_bounds = _text_label_cell_bounds(
                            rows[row], line, column, source_image.width
                        )
                    if x_bounds is None:
                        x_bounds = _column_bounds(
                            centers or [], layout, column, source_image.width
                        )
                    y_bounds = (
                        _numeric_word_y_bounds(line, x_bounds, source_image.height)
                        if line is not None and x_bounds is not None
                        else None
                    )
                    if line is None:
                        cell_refusal = "row_alignment_missing"
                    elif x_bounds is None:
                        cell_refusal = "column_alignment_missing"
                    else:
                        cell_refusal = None
                    if cell_refusal:
                        refusals.append({
                            **cell,
                            "source_sha256": source_sha256,
                            "reason": cell_refusal,
                        })
                        continue
                    if y_bounds is None:
                        y_bounds = (
                            max(0, min(int(word["top"]) for word in line) - 1),
                            min(
                                source_image.height,
                                max(
                                    int(word["top"]) + int(word["height"])
                                    for word in line
                                )
                                + 1,
                            ),
                        )
                    top, bottom = y_bounds
                    box = (x_bounds[0], top, x_bounds[1], bottom)
                    image = source_image.crop(box)
                    image = image.resize(
                        (image.width * scale, image.height * scale),
                        Image.Resampling.LANCZOS,
                    )
                    stem = block_id.strip("#/").replace("/", "_")
                    filename = f"{source_sha256[:16]}_{stem}_r{row}_c{column}.png"
                    image.save(output_dir / filename)
                    crops.append({
                        "source_sha256": source_sha256,
                        "page": cell["page"],
                        "block_id": block_id,
                        "source_row": row,
                        "source_column": column,
                        "expected": cell["expected"],
                        "label": cell.get("label"),
                        "source_crop": str(source_crop),
                        "source_crop_sha256": source_crop_sha256,
                        "source_box": list(box),
                        "scale": scale,
                        "interpolation": "lanczos",
                        "path": filename,
                        "width": image.width,
                        "height": image.height,
                    })
                    block_written += 1
            print(
                f"[{completed}/{len(cells_by_block)}] {block_id}: "
                f"{block_written}/{len(cells)} cells",
                flush=True,
            )
    manifest = {
        "schema_version": 1,
        "producer": "scripts/prepare_paddleocr_crops.py",
        "scale": scale,
        "interpolation": "lanczos",
        "labels": labels,
        "crops": crops,
        "refusals": refusals,
    }
    (output_dir / "crops.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def write_source_box_crops(
    out_dir: Path,
    labels: dict,
    source_boxes: dict,
    output_dir: Path,
    scale: int,
) -> dict:
    if source_boxes.get("schema_version") != 1:
        raise ValueError("unsupported source-box schema_version")
    label_cells = {
        (
            document["source_sha256"],
            cell["block_id"],
            int(cell["row"]),
            int(cell["column"]),
        ): (document, cell)
        for document in labels.get("documents", [])
        for cell in document.get("cells", [])
    }
    box_cells = {
        (
            document["source_sha256"],
            cell["block_id"],
            int(cell["row"]),
            int(cell["column"]),
        ): (document, cell)
        for document in source_boxes.get("documents", [])
        for cell in document.get("cells", [])
    }
    if box_cells.keys() != label_cells.keys():
        missing = sorted(label_cells.keys() - box_cells.keys())
        extra = sorted(box_cells.keys() - label_cells.keys())
        raise ValueError(f"source boxes and labels diverged: missing={missing}, extra={extra}")

    output_dir.mkdir(parents=True, exist_ok=True)
    crops = []
    for completed, key in enumerate(label_cells, start=1):
        label_document, label = label_cells[key]
        box_document, box_record = box_cells[key]
        document_out_dir = out_dir / label_document.get("out_dir", "")
        version_dir = _version_dir(document_out_dir, label_document)
        if version_dir is None:
            raise ValueError(f"conversion version unavailable: {key}")
        source_crop = _table_crops(version_dir).get(label["block_id"])
        if source_crop is None or not source_crop.is_file():
            raise ValueError(f"source crop unavailable: {key}")
        source_crop_sha256 = _sha256(source_crop)
        if source_crop_sha256 != box_record["source_crop_sha256"]:
            raise ValueError(f"source crop hash mismatch: {key}")
        source_box = tuple(int(value) for value in box_record["source_box"])
        if len(source_box) != 4:
            raise ValueError(f"source box must have four coordinates: {key}")

        with Image.open(source_crop) as source_image:
            left, top, right, bottom = source_box
            if not (
                0 <= left < right <= source_image.width
                and 0 <= top < bottom <= source_image.height
            ):
                raise ValueError(f"source box outside crop: {key}")
            image = source_image.crop(source_box)
            image = image.resize(
                (image.width * scale, image.height * scale),
                Image.Resampling.LANCZOS,
            )
            stem = label["block_id"].strip("#/").replace("/", "_")
            filename = (
                f"{label_document['source_sha256'][:16]}_{stem}_"
                f"r{label['row']}_c{label['column']}.png"
            )
            crop_path = output_dir / filename
            image.save(crop_path)
            width, height = image.size

        record_id = (
            f"{label_document['source_sha256'][:16]}:{label['block_id']}:"
            f"r{label['row']}:c{label['column']}"
        )
        crops.append({
            "id": record_id,
            "source_sha256": label_document["source_sha256"],
            "page": label["page"],
            "block_id": label["block_id"],
            "source_row": label["row"],
            "source_column": label["column"],
            "expected": label["expected"],
            "label": label.get("label"),
            "source_crop": str(source_crop),
            "source_crop_sha256": source_crop_sha256,
            "source_box": list(source_box),
            "geometry_method": "human_verified_source_pixel_box",
            "scale": scale,
            "interpolation": "lanczos",
            "path": filename,
            "crop_sha256": _sha256(crop_path),
            "width": width,
            "height": height,
        })
        print(f"[{completed}/{len(label_cells)}] {record_id}", flush=True)

    manifest = {
        "schema_version": 1,
        "producer": "scripts/prepare_paddleocr_crops.py",
        "method": "human_verified_source_pixel_boxes",
        "contract": {
            "geometry": "source-pixel rectangles verified before recognition; no OCR token geometry",
            "recognizer_input": "crop image and opaque cell id only",
        },
        "scale": scale,
        "interpolation": "lanczos",
        "source_boxes_sha256": source_boxes.get("sha256"),
        "labels": labels,
        "crops": crops,
        "refusals": [],
    }
    (output_dir / "crops.json").write_text(json.dumps(manifest, indent=2) + "\n")
    inputs = {
        "schema_version": 1,
        "records": [
            {
                "id": crop["id"],
                "crop": crop["path"],
                "crop_sha256": crop["crop_sha256"],
            }
            for crop in crops
        ],
    }
    (output_dir / "inputs.json").write_text(json.dumps(inputs, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write labelled numeric-cell crops for PaddleOCR-VL evaluation."
    )
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--labels", type=Path, default=_LABELS)
    parser.add_argument(
        "--reader-disagreements",
        type=Path,
        help="Write only cells where a preserved numeric report has two disagreeing reads.",
    )
    parser.add_argument("--scale", type=int, default=5)
    parser.add_argument(
        "--source-boxes",
        type=Path,
        help="Use hash-pinned human-verified source-pixel boxes instead of Tesseract geometry.",
    )
    parser.add_argument(
        "--max-cells",
        type=int,
        help="Limit crops with deterministic round-robin sampling across source tables.",
    )
    parser.add_argument("--tesseract", default="tesseract")
    args = parser.parse_args()

    labels = json.loads(args.labels.read_text())
    if args.source_boxes:
        source_boxes = json.loads(args.source_boxes.read_text())
        source_boxes["sha256"] = _sha256(args.source_boxes)
        manifest = write_source_box_crops(
            args.out_dir, labels, source_boxes, args.output_dir, args.scale
        )
        print(
            f"Paddle crops: {len(manifest['crops'])} written, "
            f"{len(manifest['refusals'])} tool-refused"
        )
        return

    executable = shutil.which(args.tesseract)
    if executable is None:
        print(f"Tesseract crop locator unavailable: {args.tesseract}")
        raise SystemExit(2)
    if args.reader_disagreements:
        labels = _reader_disagreement_labels(
            labels, json.loads(args.reader_disagreements.read_text())
        )
    if args.max_cells is not None:
        if args.max_cells < 1:
            parser.error("--max-cells must be at least 1")
        labels = _limit_labels(labels, args.max_cells)
    manifest = write_crops(
        args.out_dir, labels, args.output_dir, executable, args.scale
    )
    print(
        f"Paddle crops: {len(manifest['crops'])} written, "
        f"{len(manifest['refusals'])} tool-refused"
    )


if __name__ == "__main__":
    main()
