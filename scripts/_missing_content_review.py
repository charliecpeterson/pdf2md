"""Local source-review sheet for the missing-content probe; writes only new outputs."""

from __future__ import annotations

import html
import json
import os
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

import pypdfium2 as pdfium


def link(path: Path, output: Path) -> str:
    return html.escape(quote(os.path.relpath(path, output)))


def write_review(report: dict, output: Path) -> None:
    sections = []
    for index, record in enumerate(report["sample"]):
        source, bundle = Path(record["source"]), Path(record["bundle"])
        filename = f"page-{index:04d}.png"
        with closing(pdfium.PdfDocument(source)) as pdf:
            page = pdf[record["page"] - 1]
            try:
                bitmap = page.render(scale=2)
                try:
                    bitmap.to_pil().save(output / filename)
                finally:
                    bitmap.close()
            finally:
                page.close()
        passages = []
        markdown_paths = set()
        path = bundle / "passages.jsonl"
        if path.exists():
            for line in path.read_text().splitlines():
                passage = json.loads(line)
                if any(s["page"] == record["page"] for s in passage["sources"]):
                    passages.append(passage["display_text"])
                    if passage.get("markdown"):
                        markdown_paths.add(bundle / passage["markdown"])
        entry = bundle / "document.md"
        if not entry.exists():
            entry = bundle / "index.md"
        markdown_paths.add(entry)
        output_links = " | ".join(
            f'<a href="{link(path, output)}" target="_blank">{html.escape(path.name)}</a>'
            for path in sorted(markdown_paths) if path.is_file()
        ) or "No Markdown entry point found; inspect the bundle directly."
        text = "\n\n".join(passages) or "No page passages available. Inspect the full output and its assets."
        hints = {key: record[key] for key in
                 ("glyph_check", "baseline_reasons", "candidates")}
        sections.append(f"""
<section data-index="{index}">
<h2>{html.escape(bundle.parent.name)} / page {record['page']}</h2>
<p><a href="{link(source, output)}#page={record['page']}" target="_blank">Source PDF</a> |
Output: {output_links}</p>
<div class="comparison"><a href="{filename}" target="_blank"><img src="{filename}" loading="lazy"
alt="Source page {record['page']}"></a><pre>{html.escape(text)}</pre></div>
<p>Page attribution may miss continued paragraphs. Check the full output, neighbouring sections,
and linked crops before declaring content missing.</p>
<label>Meaningful source content omitted?
<select data-field="omission"><option>unreviewed</option><option>yes</option><option>no</option>
<option>uncertain</option></select></label>
<label>Reviewer <input data-field="reviewer" autocomplete="off"></label>
<label>Source evidence and output location checked <textarea data-field="note" rows="3"></textarea></label>
<details><summary>Detector hints (open after independent reading)</summary>
<pre>{html.escape(json.dumps(hints, indent=2, ensure_ascii=False))}</pre></details>
</section>""")
    payload = json.dumps({
        "schema_version": 1, "experiment_id": report["experiment_id"],
        "labels": [{"id": p["id"], "omission": "unreviewed", "reviewer": "", "note": ""}
                   for p in report["sample"]],
    }).replace("<", "\\u003c")
    page = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Missing-content source review</title>
<style>
body{font:16px/1.5 system-ui,sans-serif;max-width:1500px;margin:auto;padding:24px;color:#17202b}
section{border-top:2px solid #bbb;padding:16px 0;margin-top:24px}
.comparison{display:grid;grid-template-columns:1fr 1fr;gap:24px;align-items:start}
img{width:100%;border:1px solid #ddd}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.5 monospace}
label{display:block;margin:12px 0}textarea{display:block;width:95%}button{padding:10px}
@media(max-width:800px){.comparison{grid-template-columns:1fr}}
</style><h1>Missing-content source review</h1>
<p>Compare each source page with the output, including its preserved crops. Label <b>yes</b>
only when meaningful content is absent from both text and content fallback crops. General
page-evidence images are not content fallback crops. Ignore deliberately
dropped running headers, page numbers, and decoration. Wrong text, ordering, and numerical
errors are separate questions. Use <b>uncertain</b> when the evidence is insufficient.</p>
<p>This sample includes flagged and unflagged pages. No labels are inferred from detector results.
Download your labels before closing or reloading; this page does not save automatically.</p>
<button id="download">Download labels.json</button>
""" + "\n".join(sections) + """
<script type="application/json" id="initial">""" + payload + """</script>
<script>
const data = JSON.parse(document.getElementById('initial').textContent);
document.getElementById('download').addEventListener('click', () => {
  document.querySelectorAll('section[data-index]').forEach(section => {
    const record = data.labels[Number(section.dataset.index)];
    section.querySelectorAll('[data-field]').forEach(input => { record[input.dataset.field] = input.value; });
  });
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2) + '\\n'], {type:'application/json'}));
  const a = document.createElement('a'); a.href = url; a.download = 'labels.json'; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
</script></html>
"""
    (output / "review.html").write_text(page)
