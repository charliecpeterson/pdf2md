"""Read-only scan-omission experiment. See docs/scan-omission-pilot.md."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from contextlib import closing
from importlib.metadata import version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pypdfium2 as pdfium
from _missing_content_review import link
from _scan_omissions import POLICY, compare_lines, read_lines
from probe_missing_content import bundle_hashes, page_blocks, sha256, write_json

from pdf2md.render import CropRenderer
from pdf2md.schema import BBox


def write_review(report: dict, bundle: Path, output: Path, source: Path) -> None:
    blocks = {b["id"]: b for b in json.loads((bundle / "provenance.json").read_text())["blocks"]}
    entry = bundle / ("document.md" if (bundle / "document.md").exists() else "index.md")
    parts = ["<!doctype html><meta charset='utf-8'><title>Scan reader disagreements</title>",
             "<style>body{font:16px system-ui;max-width:1100px;margin:auto;padding:24px}"
             "img{max-width:100%}pre{white-space:pre-wrap}section{border-top:1px solid #888}</style>",
             "<h1>Scan reader disagreements</h1><p>Review hints, not verified omissions. "
             "Compare the source crop, full page, output text, and content fallback crops.</p>",
             f'<p><a href="{link(entry, output)}">Full Markdown</a> | '
             '<a href="report.json">All lines, exclusions, and unassessed evidence</a></p>']
    with CropRenderer(source, dpi=300, padding_pts=8) as renderer:
        for page in report["pages"]:
            for line in page["lines"]:
                if line["status"] != "reader_disagreement":
                    continue
                name = f"candidate-{page['page']:03d}-{line['line_id'].replace(':', '-')}.png"
                x0, y0, x1, y1 = line["bbox"]
                renderer.crop(page["page"], BBox(x0, y1, x1, y0), output / name)
                line["review_crop"] = name
                text = "\n\n".join(blocks[k]["text"] for k in line["block_ids"])
                parts.append(
                    f"<section><h2>Page {page['page']}, line {html.escape(line['line_id'])}</h2>"
                    f'<p><a href="page-{page["page"]:03d}.png">Full source page</a></p>'
                    f'<img src="{name}" alt="Source line and surrounding context">'
                    f"<p>Reader: {html.escape(line['text'])}</p>"
                    f"<p>Normalized distance: {line['distance']}</p>"
                    f"<pre>{html.escape(text) or 'No intersecting emitted text block.'}</pre></section>"
                )
    (output / "review.html").write_text("\n".join(parts))


def content_crop(block: dict, bundle: Path, markdown: str) -> bool:
    crop = block.get("extra", {}).get("crop_path")
    if not crop or re.fullmatch(r"page_\d+\.png", Path(crop).name):
        return False
    path = (bundle / crop).resolve()
    return (path.is_relative_to(bundle.resolve()) and path.is_file()
            and re.search(r"!\[[^\]\n]*\]\(" + re.escape(crop) + r"\)", markdown) is not None)


def scan(bundle: Path, output: Path, tessdata: Path, executable: str = "tesseract") -> dict:
    bundle, output, tessdata = bundle.resolve(), output.resolve(), tessdata.resolve()
    if output.exists() or output.is_relative_to(bundle.parent):
        raise ValueError("use a new output directory outside the source bundle's document directory")
    reader = shutil.which(executable)
    if reader is None:
        raise FileNotFoundError(executable)
    trained = tessdata / "eng.traineddata"
    source = bundle.parent / "source.pdf"
    provenance = json.loads((bundle / "provenance.json").read_text())
    if sha256(source) != provenance["source_sha256"]:
        raise ValueError("source hash differs from provenance")
    reader_version = subprocess.run([reader, "--version"], capture_output=True, text=True,
                                    check=True, timeout=10).stdout.splitlines()[0]
    manifest = {
        "schema_version": 1, "purpose": "development second-reader disagreements, not omissions",
        "source": str(source), "source_sha256": sha256(source), "bundle": str(bundle),
        "bundle_files": bundle_hashes(bundle), "policy": POLICY,
        "reader": {"executable": reader, "sha256": sha256(Path(reader)),
                   "version": reader_version, "trained_data": str(trained),
                   "trained_data_sha256": sha256(trained)},
        "implementation_sha256": {
            **{name: sha256(Path(__file__).parent / name) for name in
            ("probe_scan_omissions.py", "_scan_omissions.py", "probe_missing_content.py",
             "_missing_content.py", "_missing_content_review.py")},
            "src/pdf2md/render.py": sha256(Path(__file__).parents[1] / "src/pdf2md/render.py")},
        "runtime": {"python": sys.version, "pypdfium2": version("pypdfium2"),
                    "pillow": version("pillow")},
    }
    blocks = page_blocks(provenance["blocks"])
    markdown = "\n".join(path.read_text() for path in bundle.glob("*.md"))
    for group in blocks.values():
        for block in group:
            block["content_crop"] = content_crop(block, bundle, markdown)
    output.mkdir(parents=True)
    write_json(output / "manifest.json", manifest)
    pages = []
    with closing(pdfium.PdfDocument(source)) as pdf:
        if len(pdf) != provenance["page_count"]:
            raise ValueError("page count differs from provenance")
        for index in range(len(pdf)):
            number = index + 1
            record = {"page": number, "status": "pending", "lines": []}
            pages.append(record)
            with closing(pdf[index]) as page:
                if page.get_rotation() != 0:
                    record["status"] = "unsupported_rotation"
                    continue
                image_path = output / f"page-{number:03d}.png"
                with closing(page.render(scale=POLICY["dpi"] / 72)) as bitmap:
                    with bitmap.to_pil() as image:
                        image.save(image_path)
                        size = image.size
                command = [reader, str(image_path), "stdout", "--tessdata-dir", str(tessdata),
                           "--psm", str(POLICY["psm"]), "-l", POLICY["language"], "tsv"]
                try:
                    run = subprocess.run(command, capture_output=True, text=True, check=False,
                                         timeout=POLICY["timeout_seconds"],
                                         env={**os.environ, "OMP_THREAD_LIMIT": "2"})
                except subprocess.TimeoutExpired:
                    record["status"] = "reader_timeout"
                    continue
                record.update(command=command, reader_stderr=run.stderr,
                              source_image_sha256=sha256(image_path))
                if run.returncode:
                    record.update(status="reader_failed", exit_code=run.returncode)
                    continue
                tsv_path = output / f"page-{number:03d}.tsv"
                tsv_path.write_text(run.stdout)
                record["reader_tsv_sha256"] = sha256(tsv_path)
                try:
                    lines = read_lines(run.stdout, *size, page.get_bbox())
                except (ValueError, KeyError, TypeError) as exc:
                    record.update(status="invalid_reader_output", error=str(exc))
                    continue
                record.update(status="compared" if lines else "no_reader_lines",
                              lines=compare_lines(lines, blocks[number]))
            print(f"page {number}/{len(pdf)}: {record['status']}", flush=True)
    if bundle_hashes(bundle) != manifest["bundle_files"] or sha256(source) != manifest["source_sha256"]:
        raise ValueError("source or bundle changed during the experiment")
    report = {
        "schema_version": 1, "manifest_sha256": sha256(output / "manifest.json"),
        "pages": pages, "summary": {
            "pages": len(pages), "page_status": dict(Counter(p["status"] for p in pages)),
            "line_status": dict(Counter(line["status"] for p in pages for line in p["lines"])),
        },
        "limits": "Reader agreements are not verified completeness. Disagreements need source review. "
        "Both readers can omit the same text. No extraction or production warnings were changed.",
    }
    write_review(report, bundle, output, source)
    write_json(output / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tessdata-dir", type=Path, required=True)
    args = parser.parse_args()
    report = scan(args.bundle, args.output, args.tessdata_dir)
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
