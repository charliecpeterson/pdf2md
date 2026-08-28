"""Evaluate conservative two-pass OCR evidence for text-valued table row keys."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from eval_numeric_tables import _candidate_tables, _table_pages
from pdf2md.table_verify import (
    _aligned_tesseract_lines,
    _numeric_source_rows,
    _numericish_word,
    _table_layout,
)


_ROOT = Path(__file__).parent.parent
_GROUND_TRUTH = _ROOT / "tests" / "scan_degradation_ground_truth.json"
_SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
_CONFUSABLE_GLYPHS = frozenset("Il1|")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).translate(_SUBSCRIPTS)
    return re.sub(r"[^a-z0-9]", "", normalized.casefold())


def _outcome(actual: str | None, expected: str) -> str:
    if actual is None:
        return "tool_refused"
    return "agree" if _key(actual) == _key(expected) else "disagree"


def _counts(records: list[dict], field: str) -> dict[str, int]:
    return {
        outcome: sum(record[field] == outcome for record in records)
        for outcome in ("agree", "disagree", "tool_refused")
    }


def _table_crops(version_dir: Path) -> dict[str, Path]:
    manifest = json.loads((version_dir / "manifest.json").read_text())
    return {
        record["block_id"]: version_dir / record["crop"]
        for record in manifest.get("representations", {}).get("tables", [])
        if record.get("crop")
    }


def _run_tesseract(
    executable: str, image_path: Path, psm: int
) -> str:
    completed = subprocess.run(
        [executable, str(image_path), "stdout", "--psm", str(psm), "-l", "eng", "tsv"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if completed.returncode:
        raise RuntimeError(
            f"Tesseract exited {completed.returncode}: {completed.stderr.strip()}"
        )
    return completed.stdout


def _label_words(line: list[dict[str, object]]) -> list[dict[str, object]]:
    words = sorted(line, key=lambda word: float(word["x"]))
    first_numeric = next(
        (
            index
            for index, word in enumerate(words)
            if _numericish_word(str(word["text"]))
        ),
        len(words),
    )
    return words[:first_numeric]


def _joined_words(words: list[dict[str, object]]) -> str | None:
    value = " ".join(str(word["text"]) for word in words).strip()
    return value or None


def _label_crop(image: Image.Image, words: list[dict[str, object]]) -> Image.Image:
    left = min(int(word["left"]) for word in words)
    top = min(int(word["top"]) for word in words)
    right = max(int(word["left"]) + int(word["width"]) for word in words)
    bottom = max(int(word["top"]) + int(word["height"]) for word in words)
    height = max(bottom - top, 1)
    pad_x = max(6, round(height * 0.4))
    pad_y = max(4, round(height * 0.25))
    crop = image.crop((
        max(0, left - pad_x),
        max(0, top - pad_y),
        min(image.width, right + pad_x),
        min(image.height, bottom + pad_y),
    ))
    crop = ImageOps.autocontrast(ImageOps.grayscale(crop))
    crop = crop.resize((crop.width * 4, crop.height * 4), Image.Resampling.LANCZOS)
    return crop.filter(ImageFilter.UnsharpMask(radius=1, percent=100, threshold=3))


def _service_crop(crop: Image.Image) -> Image.Image:
    width = max(640, crop.width)
    height = max(192, crop.height)
    canvas = Image.new("RGB", (width, height), "white")
    canvas.paste(crop, ((width - crop.width) // 2, (height - crop.height) // 2))
    return canvas


def _page_words(tsv: str) -> dict[int, str | None]:
    pages: dict[int, list[tuple[int, str]]] = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") != "5" or not row.get("text", "").strip():
            continue
        page = int(row["page_num"])
        pages.setdefault(page, []).append((int(row["left"]), row["text"].strip()))
    return {
        page: " ".join(text for _, text in sorted(words)).strip() or None
        for page, words in pages.items()
    }


def _consensus(table_read: str | None, isolated_read: str | None) -> str | None:
    if not table_read or not isolated_read or _key(table_read) != _key(isolated_read):
        return None
    return table_read


def _verified_key(
    primary: str | None,
    table_read: str | None,
    isolated_read: str | None,
) -> tuple[str | None, str]:
    if primary is None:
        return None, "primary_missing"
    if table_read is None or isolated_read is None:
        return None, "reader_missing"
    consensus = _consensus(table_read, isolated_read)
    if consensus is None:
        return None, "reader_disagreement"
    if _key(primary) != _key(consensus):
        return None, "primary_reader_disagreement"
    if any(character in _CONFUSABLE_GLYPHS for character in primary + table_read + isolated_read):
        return None, "confusable_glyph"
    return primary, "three_way_agreement"


def _paddle_read(response: dict) -> tuple[str | None, str | None]:
    if response.get("errorCode") != 0:
        return None, "paddle_service_error"
    contents = []
    for page in (response.get("result") or {}).get("layoutParsingResults", []):
        pruned = page.get("prunedResult") or {}
        blocks = pruned.get("parsing_res_list", []) if isinstance(pruned, dict) else []
        contents.extend(
            str(block.get("block_content", "")).strip()
            for block in blocks
            if str(block.get("block_content", "")).strip()
        )
    if not contents:
        return None, "paddle_text_missing"
    if len(contents) != 1 or not _key(contents[0]):
        return None, "ambiguous_paddle_text"
    return contents[0], None


def add_paddle_results(report: dict, run_path: Path) -> dict:
    run = json.loads(run_path.read_text())
    entries = {
        (entry["variant"], entry["source_row"]): entry
        for entry in run.get("results", [])
    }
    refusals = Counter()
    for record in report["records"]:
        entry = entries.get((record["variant"], record["source_row"]))
        value = None
        refusal = None
        if record["verified_outcome"] == "agree":
            refusal = "not_routed"
        elif entry is None:
            refusal = "run_entry_missing"
        else:
            response_path = run_path.parent / entry.get("response", "")
            input_path = run_path.parent / entry.get("input", "")
            if not response_path.is_file():
                refusal = "response_missing"
            elif entry.get("response_sha256") != _sha256(response_path):
                refusal = "response_hash_mismatch"
            elif not input_path.is_file() or entry.get("input_sha256") != _sha256(input_path):
                refusal = "input_hash_mismatch"
            elif entry.get("http_status") != 200 or entry.get("error_code") != 0:
                refusal = "paddle_request_failed"
            else:
                value, refusal = _paddle_read(json.loads(response_path.read_text()))
        record["paddle_value"] = value
        record["paddle_outcome"] = _outcome(value, record["expected"])
        record["paddle_refusal_reason"] = refusal
        if refusal and refusal != "not_routed":
            refusals[refusal] += 1
    routed = [
        record for record in report["records"]
        if record["paddle_refusal_reason"] != "not_routed"
    ]
    report["paddle"] = {
        "reader": "PaddleOCR-VL 1.6 layout-parsing service",
        "run": str(run_path),
        "tool": run.get("tool") or {},
        "routed": len(routed),
        **_counts(routed, "paddle_outcome"),
        "refusal_reasons": dict(refusals),
    }
    return report


def _read_keys(
    rows: list[list[str]],
    crop_path: Path,
    executable: str,
    output_dir: Path | None = None,
    prefix: str = "key",
) -> dict[int, dict[str, str | None]]:
    layout = _table_layout(rows, None)
    if layout is None or len(layout.starts) != 1:
        return {}
    table_tsv = _run_tesseract(executable, crop_path, 6)
    aligned = _aligned_tesseract_lines(rows, table_tsv, layout)
    label_words = {
        row_index: _label_words(line)
        for row_index, line in aligned
    }
    label_words = {
        row_index: words
        for row_index, words in label_words.items()
        if words
    }
    if not label_words:
        return {}

    source_rows = sorted(label_words)
    with Image.open(crop_path) as image, tempfile.TemporaryDirectory(
        prefix="pdf2md-key-reader-"
    ) as temp_dir:
        crops = [_label_crop(image, label_words[row_index]) for row_index in source_rows]
        crop_paths = {}
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            for row_index, crop in zip(source_rows, crops, strict=True):
                output_path = output_dir / f"{prefix}_r{row_index}.png"
                service_crop = _service_crop(crop)
                service_crop.save(output_path)
                service_crop.close()
                crop_paths[row_index] = str(output_path)
        multipage = Path(temp_dir) / "key-crops.tiff"
        first, *rest = crops
        first.save(multipage, save_all=True, append_images=rest, compression="tiff_lzw")
        for crop in crops:
            crop.close()
        isolated = _page_words(_run_tesseract(executable, multipage, 8))

    readings = {}
    for page, row_index in enumerate(source_rows, start=1):
        table_read = _joined_words(label_words[row_index])
        isolated_read = isolated.get(page)
        readings[row_index] = {
            "table_psm6": table_read,
            "isolated_psm8": isolated_read,
            "consensus": _consensus(table_read, isolated_read),
            "crop_path": crop_paths.get(row_index),
        }
    return readings


def evaluate(
    version_dir: Path,
    ground_truth: dict,
    corpus_manifest: dict,
    executable: str,
    crop_dir: Path | None = None,
) -> dict:
    rows_by_block = _candidate_tables(version_dir)
    pages = _table_pages(version_dir)
    crops = _table_crops(version_dir)
    block_by_page = {page: block_id for block_id, page in pages.items()}
    expected_rows = ground_truth["rows"]
    records = []
    variant_reports = []

    for variant in corpus_manifest["variants"]:
        page = variant["page"]
        block_id = block_by_page.get(page)
        rows = rows_by_block.get(block_id, []) if block_id else []
        crop_path = crops.get(block_id) if block_id else None
        readings = (
            _read_keys(rows, crop_path, executable, crop_dir, variant["id"])
            if crop_path is not None and crop_path.is_file()
            else {}
        )
        source_rows = _numeric_source_rows(rows)
        variant_records = []
        for position, expected_row in enumerate(expected_rows):
            source_row = source_rows[position] if position < len(source_rows) else None
            primary = rows[source_row][0] if source_row is not None else None
            reading = readings.get(source_row, {}) if source_row is not None else {}
            table_read = reading.get("table_psm6")
            isolated_read = reading.get("isolated_psm8")
            consensus = reading.get("consensus")
            verified, refusal_reason = _verified_key(
                primary, table_read, isolated_read
            )
            variant_records.append({
                "variant": variant["id"],
                "page": page,
                "position": position,
                "source_row": source_row,
                "expected": expected_row["key"],
                "primary": primary,
                "primary_outcome": _outcome(primary, expected_row["key"]),
                "table_psm6": table_read,
                "table_psm6_outcome": _outcome(table_read, expected_row["key"]),
                "isolated_psm8": isolated_read,
                "isolated_psm8_outcome": _outcome(isolated_read, expected_row["key"]),
                "reader_consensus": consensus,
                "reader_consensus_outcome": _outcome(consensus, expected_row["key"]),
                "verified_key": verified,
                "verified_outcome": _outcome(verified, expected_row["key"]),
                "verification_basis": refusal_reason,
                "crop_path": reading.get("crop_path"),
            })
        variant_reports.append({
            "id": variant["id"],
            "page": page,
            "checked": len(variant_records),
            "primary": _counts(variant_records, "primary_outcome"),
            "table_psm6": _counts(variant_records, "table_psm6_outcome"),
            "isolated_psm8": _counts(variant_records, "isolated_psm8_outcome"),
            "reader_consensus": _counts(variant_records, "reader_consensus_outcome"),
            "verified": _counts(variant_records, "verified_outcome"),
        })
        records.extend(variant_records)

    return {
        "schema_version": 1,
        "method": "two_pass_tesseract_single_panel_row_keys",
        "contract": {
            "reference": "source-pinned row keys in fixed source order",
            "reader": "whole-table PSM 6 plus isolated-key PSM 8",
            "acceptance": (
                "primary and both reads normalize identically, excluding unresolved "
                "I/l/1/bar glyphs"
            ),
            "normalization": "Unicode compatibility, case, subscripts, punctuation; I/l remain distinct",
            "outcomes": ["agree", "disagree", "tool_refused"],
        },
        "source": ground_truth["source"],
        "version_dir": str(version_dir),
        "reader_executable": executable,
        "checked": len(records),
        "primary": _counts(records, "primary_outcome"),
        "table_psm6": _counts(records, "table_psm6_outcome"),
        "isolated_psm8": _counts(records, "isolated_psm8_outcome"),
        "reader_consensus": _counts(records, "reader_consensus_outcome"),
        "verified": _counts(records, "verified_outcome"),
        "verification_basis": dict(Counter(
            record["verification_basis"] for record in records
        )),
        "variants": variant_reports,
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate conservative independent OCR of table row keys."
    )
    parser.add_argument("version_dir", type=Path)
    parser.add_argument("--ground-truth", type=Path, default=_GROUND_TRUTH)
    parser.add_argument("--corpus-manifest", type=Path, required=True)
    parser.add_argument("--tesseract-executable", default="tesseract")
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--paddle-manifest",
        type=Path,
        help="Write refused key crops for a selective PaddleOCR-VL run.",
    )
    parser.add_argument(
        "--paddle-run",
        type=Path,
        help="Score preserved PaddleOCR-VL responses for refused keys.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit nonzero on any wrong confirmation or refusal.",
    )
    parser.add_argument("--check", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    executable = shutil.which(args.tesseract_executable)
    if executable is None:
        raise SystemExit(f"Tesseract executable not found: {args.tesseract_executable}")
    crop_dir = (
        args.paddle_manifest.parent / "crops"
        if args.paddle_manifest is not None
        else None
    )
    report = evaluate(
        args.version_dir,
        json.loads(args.ground_truth.read_text()),
        json.loads(args.corpus_manifest.read_text()),
        executable,
        crop_dir,
    )
    if args.paddle_run:
        add_paddle_results(report, args.paddle_run)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    if args.paddle_manifest:
        args.paddle_manifest.parent.mkdir(parents=True, exist_ok=True)
        crops = []
        for record in report["records"]:
            crop_path = Path(record["crop_path"]) if record.get("crop_path") else None
            if record["verified_outcome"] != "tool_refused" or crop_path is None:
                continue
            crops.append({
                key: record[key]
                for key in (
                    "variant", "page", "position", "source_row", "expected",
                    "primary", "table_psm6", "isolated_psm8", "verification_basis",
                )
            } | {
                "path": crop_path.relative_to(args.paddle_manifest.parent).as_posix(),
                "input_sha256": _sha256(crop_path),
            })
        args.paddle_manifest.write_text(json.dumps({
            "schema_version": 1,
            "producer": "scripts/eval_table_keys.py",
            "source_report": str(args.report) if args.report else None,
            "crops": crops,
        }, indent=2) + "\n")
    verified = report["verified"]
    print(
        f"table keys: {verified['agree']}/{report['checked']} verified, "
        f"{verified['disagree']} wrong confirmations, "
        f"{verified['tool_refused']} tool-refused"
    )
    for variant in report["variants"]:
        counts = variant["verified"]
        print(
            f"  {variant['id']}: {counts['agree']}/{variant['checked']} verified, "
            f"{counts['disagree']} wrong, {counts['tool_refused']} refused"
        )
    if (args.strict or args.check) and (
        verified["disagree"] or verified["tool_refused"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
