"""Build local review sheets for calibrating extracted numeric table cells.

The sheet samples cell evidence deterministically, keeps source crops linked in place,
and downloads completed labels in the same schema as the numeric-table evaluator.
"""

from __future__ import annotations

import csv
import html
import json
import os
import random
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

from PIL import Image

from pdf2md.render import CropRenderer
from pdf2md.schema import BBox


_CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2, "verified": 3}
_CSV_FIELDS = (
    "page", "block_id", "row", "column", "atomic_number", "symbol", "row_key",
    "table_column", "primary_value", "reader_value", "best_value", "confidence",
    "resolution_basis", "validator_preference", "validator_basis",
    "verification_status", "expected", "label", "reviewer", "note",
)


def create_table_review(
    version_dir: Path,
    *,
    output_path: Path | None = None,
    sample_size: int = 90,
    seed: int = 0,
    per_table: int = 3,
    labels_path: Path | None = None,
) -> dict[str, object]:
    version_dir = Path(version_dir).resolve()
    provenance_path = version_dir / "provenance.json"
    if not provenance_path.is_file():
        raise ValueError(f"not a completed pdf2md version: {version_dir}")
    if sample_size < 1:
        raise ValueError("sample_size must be at least 1")
    if per_table < 1:
        raise ValueError("per_table must be at least 1")

    provenance = json.loads(provenance_path.read_text())
    tables = _load_tables(version_dir)
    records = _load_numeric_records(version_dir)
    if not records:
        raise ValueError(
            f"no numeric cell evidence under {version_dir}; reconvert with the current pdf2md"
        )

    source_sha256 = str(provenance.get("source_sha256") or provenance.get("doc_id") or "")
    source = Path(str(provenance.get("source_path") or "source.pdf")).name
    known = _load_labels(labels_path, source_sha256) if labels_path else {}
    selected = _stratified_sample(records, sample_size, seed, per_table, set(known))
    _ensure_source_crops(version_dir, provenance, tables, selected)
    for record in selected:
        label = known.get(_cell_key(record))
        record["expected"] = label.get("expected", "") if label else ""
        record["label"] = label.get("label", "") if label else ""
        record["reviewer"] = label.get("reviewer", "") if label else ""
        record["note"] = label.get("note", "") if label else ""

    output_path = (output_path or version_dir / "table-review.html").resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path = output_path.with_suffix(".csv")
    _write_csv(csv_path, selected)
    previews = _prepare_previews(output_path, tables, records, selected)
    output_path.write_text(
        _render_html(
            version_dir,
            output_path,
            source_sha256,
            source,
            tables,
            records,
            selected,
            previews,
            seed,
            per_table,
        )
    )
    return {
        "html": output_path,
        "csv": csv_path,
        "available": len(records),
        "sampled": len(selected),
        "confidence_counts": dict(Counter(str(row["confidence"]) for row in records)),
        "sample_counts": dict(Counter(str(row["confidence"]) for row in selected)),
        "prefilled": sum(bool(row["expected"]) for row in selected),
    }


def _ensure_source_crops(
    version_dir: Path,
    provenance: dict[str, object],
    tables: dict[str, dict[str, object]],
    selected: list[dict[str, object]],
) -> None:
    """Render missing table evidence only for cells selected into this review sheet."""
    missing = {
        str(record["source_block_id"])
        for record in selected
        if not (tables.get(str(record["source_block_id"])) or {}).get("source_crop")
    }
    if not missing:
        return
    source_pdf = version_dir.parent / "source.pdf"
    if not source_pdf.is_file():
        return
    blocks = {
        str(block.get("id")): block
        for block in provenance.get("blocks", [])
        if isinstance(block, dict) and block.get("id") in missing
    }
    renderable = [
        block_id for block_id in sorted(missing)
        if block_id in blocks and block_id in tables
    ]
    if not renderable:
        return

    asset_dir = version_dir / "assets" / "table-review"
    asset_dir.mkdir(parents=True, exist_ok=True)
    with CropRenderer(source_pdf, dpi=300) as renderer:
        for block_id in renderable:
            block = blocks[block_id]
            bbox = block.get("bbox")
            page = block.get("page")
            if not isinstance(bbox, dict) or not isinstance(page, int):
                continue
            crop_path = asset_dir / f"{block_id.strip('#/').replace('/', '_')}_p{page}.png"
            try:
                renderer.crop(page, BBox(**bbox), crop_path, dpi=300)
            except Exception:  # noqa: BLE001 - one malformed source bbox should not abort the sheet
                continue
            tables[block_id]["source_crop"] = crop_path.relative_to(version_dir).as_posix()


def _load_tables(version_dir: Path) -> dict[str, dict[str, object]]:
    tables = {}
    for path in sorted((version_dir / "data" / "tables").glob("*.json")):
        record = json.loads(path.read_text())
        block_id = record.get("block_id")
        if block_id and isinstance(record.get("rows"), list):
            record["_version_dir"] = str(version_dir)
            tables[str(block_id)] = record
    return tables


def _load_numeric_records(version_dir: Path) -> list[dict[str, object]]:
    records = []
    for path in sorted((version_dir / "data" / "tables").glob("*.cells.jsonl")):
        for line in path.read_text().splitlines():
            record = json.loads(line)
            if record.get("value_status") != "numeric":
                continue
            records.append(record)
    return records


def _load_labels(path: Path, source_sha256: str) -> dict[tuple[str, int, int], dict]:
    labels = json.loads(Path(path).read_text())
    for document in labels.get("documents", []):
        if document.get("source_sha256") != source_sha256:
            continue
        return {
            (cell["block_id"], int(cell["row"]), int(cell["column"])): cell
            for cell in document.get("cells", [])
        }
    return {}


def _cell_key(record: dict[str, object]) -> tuple[str, int, int]:
    return (
        str(record["source_block_id"]),
        int(record["source_row"]),
        int(record["source_column"]),
    )


def _stratified_sample(
    records: list[dict[str, object]],
    sample_size: int,
    seed: int,
    per_table: int,
    required: set[tuple[str, int, int]],
) -> list[dict[str, object]]:
    by_key = {_cell_key(record): record for record in records}
    selected = [by_key[key] for key in sorted(required) if key in by_key]
    selected_keys = {_cell_key(record) for record in selected}
    remaining = max(0, sample_size - len(selected))
    strata: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        if _cell_key(record) not in selected_keys:
            strata[str(record.get("confidence") or "unknown")].append(record)

    rng = random.Random(seed)
    names = sorted(strata, key=lambda name: (_CONFIDENCE_ORDER.get(name, 99), name))
    while remaining and names:
        progressed = False
        for name in list(names):
            if not remaining:
                break
            candidate = _take_diverse(strata[name], selected, per_table, rng)
            if candidate is None:
                names.remove(name)
                continue
            selected.append(candidate)
            selected_keys.add(_cell_key(candidate))
            strata[name].remove(candidate)
            remaining -= 1
            progressed = True
        if not progressed:
            break

    selected.sort(key=lambda record: (
        _CONFIDENCE_ORDER.get(str(record.get("confidence")), 99),
        int(record["page"]),
        str(record["source_block_id"]),
        int(record["source_row"]),
        int(record["source_column"]),
    ))
    return selected


def _take_diverse(
    candidates: list[dict[str, object]],
    selected: list[dict[str, object]],
    per_table: int,
    rng: random.Random,
) -> dict[str, object] | None:
    if not candidates:
        return None
    counts = Counter(str(record["source_block_id"]) for record in selected)
    under_cap = [
        record for record in candidates
        if counts[str(record["source_block_id"])] < per_table
    ]
    pool = under_cap or candidates
    return pool[rng.randrange(len(pool))]


def _write_csv(path: Path, records: list[dict[str, object]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for record in records:
            semantic = record.get("semantic_key") or {}
            writer.writerow({
                "page": record["page"],
                "block_id": record["source_block_id"],
                "row": record["source_row"],
                "column": record["source_column"],
                "atomic_number": semantic.get("atomic_number", ""),
                "symbol": semantic.get("symbol", ""),
                "row_key": semantic.get("row_key", ""),
                "table_column": semantic.get("column", ""),
                "primary_value": record.get("primary_value", ""),
                "reader_value": record.get("reader_value") or "",
                "best_value": record.get("best_value", ""),
                "confidence": record.get("confidence", ""),
                "resolution_basis": record.get("resolution_basis", ""),
                "validator_preference": record.get("validator_preference") or "",
                "validator_basis": record.get("validator_basis") or "",
                "verification_status": record.get("verification_status", ""),
                "expected": record.get("expected", ""),
                "label": record.get("label", ""),
                "reviewer": record.get("reviewer", ""),
                "note": record.get("note", ""),
            })


def _render_html(
    version_dir: Path,
    output_path: Path,
    source_sha256: str,
    source: str,
    tables: dict[str, dict[str, object]],
    all_records: list[dict[str, object]],
    selected: list[dict[str, object]],
    previews: dict[str, Path],
    seed: int,
    per_table: int,
) -> str:
    counts = Counter(str(record.get("confidence")) for record in all_records)
    sample_counts = Counter(str(record.get("confidence")) for record in selected)
    cards = "\n".join(
        _card(
            index,
            record,
            tables.get(str(record["source_block_id"])),
            previews.get(str(record["source_block_id"])),
            output_path,
        )
        for index, record in enumerate(selected)
    )
    export_records = [
        {
            "page": record["page"],
            "block_id": record["source_block_id"],
            "row": record["source_row"],
            "column": record["source_column"],
            "expected": record.get("expected", ""),
            "label": record.get("label", ""),
            "reviewer": record.get("reviewer", ""),
            "note": record.get("note", ""),
        }
        for record in selected
    ]
    export_json = json.dumps(export_records, ensure_ascii=False).replace("</", "<\\/")
    source_link = _relative_uri(version_dir.parent / "source.pdf", output_path.parent)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Numeric table review: {html.escape(source)}</title>
<style>
:root {{ color-scheme: light; --ink:#17202a; --muted:#5d6d7e; --line:#d5d8dc;
  --paper:#f7f7f3; --card:#fff; --low:#b03a2e; --medium:#9a6700; --high:#28744f; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--paper); color:var(--ink); font:15px/1.45 system-ui,sans-serif; }}
header {{ position:sticky; top:0; z-index:5; padding:16px 24px; background:#17202af2; color:white; }}
header h1 {{ margin:0 0 4px; font-size:20px; }}
header p {{ margin:0; color:#d5d8dc; }}
.toolbar {{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-top:12px; }}
button, select {{ padding:8px 12px; border:1px solid #aeb6bf; border-radius:6px; background:white; }}
button {{ cursor:pointer; font-weight:650; }}
main {{ max-width:1500px; margin:auto; padding:24px; }}
.summary {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin-bottom:20px; }}
.stat {{ background:white; border:1px solid var(--line); border-radius:8px; padding:12px; }}
.stat strong {{ display:block; font-size:22px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-left:6px solid var(--muted);
  border-radius:9px; margin:0 0 20px; overflow:hidden; }}
.card.low {{ border-left-color:var(--low); }} .card.medium {{ border-left-color:var(--medium); }}
.card.high, .card.verified {{ border-left-color:var(--high); }}
.card-head {{ display:flex; justify-content:space-between; gap:16px; padding:12px 16px; background:#fafafa; border-bottom:1px solid var(--line); }}
.badge {{ font-weight:750; text-transform:uppercase; }} .meta {{ color:var(--muted); font-family:ui-monospace,monospace; }}
.content {{ display:grid; grid-template-columns:minmax(0,1.5fr) minmax(330px,1fr); gap:18px; padding:16px; }}
.crop {{ max-width:100%; max-height:620px; border:1px solid var(--line); background:#eee; }}
.missing {{ padding:24px; border:1px dashed var(--line); color:var(--muted); }}
.values {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px; margin-bottom:12px; }}
.value {{ padding:9px; border:1px solid var(--line); border-radius:6px; min-width:0; }}
.value span {{ display:block; color:var(--muted); font-size:12px; }}
.value code {{ font-size:16px; overflow-wrap:anywhere; }}
table {{ width:100%; border-collapse:collapse; font:13px/1.3 ui-monospace,monospace; margin:12px 0; }}
td {{ border:1px solid var(--line); padding:5px; text-align:right; }} td.target {{ background:#fff1a8; outline:2px solid #8a6700; font-weight:800; }}
label {{ display:block; margin:10px 0 4px; font-weight:650; }}
input, textarea {{ width:100%; padding:8px; border:1px solid #aeb6bf; border-radius:5px; font:inherit; }}
textarea {{ min-height:64px; resize:vertical; }}
.links {{ display:flex; gap:12px; margin-top:10px; }} a {{ color:#175a8a; }}
@media (max-width:900px) {{ .content {{ grid-template-columns:1fr; }} header {{ position:static; }} }}
</style>
</head>
<body>
<header>
  <h1>Numeric table review</h1>
  <p>{html.escape(source)} · deterministic seed {seed} · maximum {per_table} sampled cells per table before fill</p>
  <div class="toolbar">
    <button id="download">Download completed labels JSON</button>
    <span id="completion">0/{len(selected)} labelled</span>
    <label for="filter" style="margin:0">Show</label>
    <select id="filter"><option value="all">all confidence levels</option><option>low</option><option>medium</option><option>high</option><option>verified</option></select>
    <a href="{source_link}" style="color:white">Open source PDF</a>
  </div>
</header>
<main>
  <section class="summary">
    <div class="stat"><strong>{len(all_records):,}</strong>numeric cells available</div>
    <div class="stat"><strong>{len(selected):,}</strong>cells sampled</div>
    <div class="stat"><strong>{sample_counts.get('low', 0):,} / {counts.get('low', 0):,}</strong>low sampled / available</div>
    <div class="stat"><strong>{sample_counts.get('medium', 0):,} / {counts.get('medium', 0):,}</strong>medium sampled / available</div>
    <div class="stat"><strong>{sample_counts.get('high', 0):,} / {counts.get('high', 0):,}</strong>high sampled / available</div>
  </section>
  {cards}
</main>
<script id="records" type="application/json">{export_json}</script>
<script>
const records = JSON.parse(document.getElementById('records').textContent);
const inputs = [...document.querySelectorAll('[data-field]')];
function sync() {{
  for (const input of inputs) records[Number(input.dataset.index)][input.dataset.field] = input.value;
  const complete = records.filter(record => record.expected.trim()).length;
  document.getElementById('completion').textContent = `${{complete}}/${{records.length}} labelled`;
}}
for (const input of inputs) input.addEventListener('input', sync);
document.getElementById('filter').addEventListener('change', event => {{
  for (const card of document.querySelectorAll('.card'))
    card.hidden = event.target.value !== 'all' && card.dataset.confidence !== event.target.value;
}});
document.getElementById('download').addEventListener('click', () => {{
  sync();
  const cells = records.filter(record => record.expected.trim());
  const payload = {{schema_version:1, documents:[{{source_sha256:{json.dumps(source_sha256)}, source:{json.dumps(source)}, cells}}]}};
  const blob = new Blob([JSON.stringify(payload, null, 2) + '\n'], {{type:'application/json'}});
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob); link.download = 'numeric-table-labels.json'; link.click();
  URL.revokeObjectURL(link.href);
}});
sync();
</script>
</body>
</html>
"""


def _card(
    index: int,
    record: dict[str, object],
    table: dict[str, object] | None,
    preview_path: Path | None,
    output_path: Path,
) -> str:
    confidence = str(record.get("confidence") or "unknown")
    block_id = str(record["source_block_id"])
    row_index = int(record["source_row"])
    column_index = int(record["source_column"])
    semantic = record.get("semantic_key") or {}
    rows = table.get("rows", []) if table else []
    row_table = _row_context(rows, row_index, column_index)
    crop = table.get("source_crop") if table else None
    crop_path = Path(str(table["_version_dir"])) / str(crop) if table and crop else None
    if crop_path is not None and crop_path.is_file():
        crop_uri = _relative_uri(crop_path, output_path.parent)
        preview_uri = _relative_uri(preview_path, output_path.parent) if preview_path else crop_uri
        crop_html = f'<a href="{crop_uri}"><img class="crop" loading="lazy" src="{preview_uri}" alt="Upright source table preview"></a>'
    else:
        crop_html = '<div class="missing">No source crop is available for this table.</div>'
    page = int(record["page"])
    version_dir = Path(str(table["_version_dir"])) if table else output_path.parent
    source_uri = _relative_uri(version_dir.parent / "source.pdf", output_path.parent) + f"#page={page}"
    description = " · ".join(filter(None, (
        str(semantic.get("symbol") or ""),
        str(semantic.get("row_key") or ""),
        str(semantic.get("column") or ""),
    )))
    return f"""<article class="card {html.escape(confidence)}" data-confidence="{html.escape(confidence)}">
<div class="card-head"><div><span class="badge">{html.escape(confidence)}</span> {html.escape(description)}</div>
<div class="meta">page {page} · row {row_index} · column {column_index}</div></div>
<div class="content"><div>{crop_html}<div class="links"><a href="{source_uri}">source page {page}</a></div></div>
<div><div class="values">
<div class="value"><span>Primary</span><code>{html.escape(str(record.get('primary_value') or ''))}</code></div>
<div class="value"><span>Reader</span><code>{html.escape(str(record.get('reader_value') or 'refused'))}</code></div>
<div class="value"><span>Best</span><code>{html.escape(str(record.get('best_value') or ''))}</code></div>
</div>
<div><strong>{html.escape(str(record.get('resolution_basis') or ''))}</strong><br>
<span class="meta">validator: {html.escape(str(record.get('validator_preference') or 'none'))} · {html.escape(str(record.get('validator_basis') or ''))}<br>{html.escape(block_id)}</span></div>
{row_table}
<label for="expected-{index}">Expected value read from source pixels</label>
<input id="expected-{index}" data-index="{index}" data-field="expected" value="{html.escape(str(record.get('expected') or ''), quote=True)}" inputmode="decimal">
<label for="label-{index}">Cell description</label>
<input id="label-{index}" data-index="{index}" data-field="label" value="{html.escape(str(record.get('label') or ''), quote=True)}">
<label for="reviewer-{index}">Reviewer</label>
<input id="reviewer-{index}" data-index="{index}" data-field="reviewer" value="{html.escape(str(record.get('reviewer') or ''), quote=True)}">
<label for="note-{index}">Notes</label>
<textarea id="note-{index}" data-index="{index}" data-field="note">{html.escape(str(record.get('note') or ''))}</textarea>
</div></div></article>"""


def _row_context(rows: object, row_index: int, column_index: int) -> str:
    if not isinstance(rows, list) or row_index >= len(rows):
        return '<div class="missing">Extracted row context is unavailable.</div>'
    rendered = []
    for index in range(max(0, row_index - 1), min(len(rows), row_index + 2)):
        row = rows[index]
        if not isinstance(row, list):
            continue
        cells = "".join(
            f'<td class="{"target" if index == row_index and column == column_index else ""}">'
            f'{html.escape(str(value))}</td>'
            for column, value in enumerate(row)
        )
        rendered.append(f"<tr>{cells}</tr>")
    return "<table aria-label=\"Extracted row context\"><tbody>" + "".join(rendered) + "</tbody></table>"


def _relative_uri(path: Path, start: Path) -> str:
    return quote(Path(os.path.relpath(path, start)).as_posix(), safe="/:#")


def _prepare_previews(
    output_path: Path,
    tables: dict[str, dict[str, object]],
    all_records: list[dict[str, object]],
    selected: list[dict[str, object]],
) -> dict[str, Path]:
    rotations: dict[str, Counter[int]] = defaultdict(Counter)
    for record in all_records:
        rotation = record.get("reader_rotation")
        if rotation is not None:
            rotations[str(record["source_block_id"])][int(rotation)] += 1

    preview_dir = output_path.parent / f"{output_path.stem}-assets"
    previews = {}
    for block_id in sorted({str(record["source_block_id"]) for record in selected}):
        table = tables.get(block_id)
        crop = table.get("source_crop") if table else None
        if not table or not crop:
            continue
        crop_path = Path(str(table["_version_dir"])) / str(crop)
        if not crop_path.is_file():
            continue
        rotation = rotations[block_id].most_common(1)[0][0] if rotations[block_id] else 0
        preview_dir.mkdir(parents=True, exist_ok=True)
        preview_path = preview_dir / f"{crop_path.stem}_r{rotation}.jpg"
        with Image.open(crop_path) as image:
            upright = image.convert("RGB").rotate(rotation, expand=True, fillcolor="white")
            upright.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            upright.save(preview_path, quality=88, optimize=True)
        previews[block_id] = preview_path
    return previews
