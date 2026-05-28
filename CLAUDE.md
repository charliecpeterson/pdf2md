# docsmcp

MCP server that transcribes PDFs/scans to markdown with per-block provenance,
indexes them for hybrid search, and exposes ~40 tools to a Claude Code client
for retrieval, citation, image crops, and visual verification.

The README has the user-facing tour. This file is for working *on* the code.

## Run and develop

```bash
uv sync                  # installs deps; first run also downloads ~1.2 GB of models
uv run docsmcp           # start the MCP server on stdio (what Claude Code spawns)
uv run python -c "from docsmcp.pipeline import transcribe; print(transcribe('/path/to.pdf'))"
```

There is no test suite yet. Verify changes by running the server against a real
PDF and checking `out/<doc_hash>/v<n>/{document.md,provenance.json}` plus the
`library.sqlite` rows. Running on `darwin` is assumed — Apple Vision OCR comes
from `ocrmac` and the visual-verify VLM uses MLX.

## Module map

```
src/docsmcp/
  server.py          MCP tool surface (~40 tools). Thin wrappers — logic lives below.
  pipeline.py        transcribe() / ingest_dir() — orchestrates engine → index → enrich.
  triage.py          Per-page born_digital/mixed/scanned/empty classification (no OCR).
  metadata.py        Heuristic title/authors/year extraction from block text.
  citation.py        APA / BibTeX formatters.
  image.py           Page + block PNG rendering via PyMuPDF, cached on disk.

  engines/
    docling.py       Primary OCR/structure engine. Returns markdown + blocks + bboxes.
    marker.py        Secondary engine, used only when verify=True. No bboxes.

  embed/
    embedder.py      mxbai-embed-large-v1 by default (DOCSMCP_EMBED_MODEL to swap).
    reranker.py      bge-reranker-v2-m3 cross-encoder, CPU only.

  postprocess/       Runs after transcription, before/during indexing.
    equations.py     LaTeX normalization + equation-label parsing.
    tables.py        Table parsing + label extraction.
    table_cells.py   Per-cell extraction for verify_table_cells.
    figures.py       Figure caption + label parsing.
    outline.py       Section tree from headings.
    crossref.py      DOI lookup → authoritative metadata.
    xref.py          In-doc cross-reference resolution ("see Fig. 3" → block_id).
    normalize.py     OCR confusable folding for search queries.
    dedup.py         Block-level dedup for repeated headers/footers.
    inference.py     Block-type inference fallbacks.
    roman.py         Roman numeral handling for page numbering.

  store/
    schema.py        Dataclasses: Block, BBox, Document, BuildInfo, PageTriage, enums.
    cache.py         out_root(), doc_dir(), versioning helpers.

  index/
    fts.py           SQLite schema + FTS5 + sqlite-vec writes + metadata mutators.
                     Also: list_docs, verify_*, table/figure/equation accessors.
    search.py        Hybrid retrieval: FTS + vec → RRF → optional rerank.

  verify/
    disagree.py      Per-page Docling-vs-Marker similarity → flags.
    vlm.py           Qwen3-VL via MLX for visual equation / metadata / cell verification.
```

## Conventions

- `doc_id` is the SHA-256 of the source file bytes (full hex). The first 16
  chars name the on-disk directory (`out/<doc_id[:16]>/v<n>/`). Don't truncate
  the id when querying SQLite.
- Re-transcribing the same file is a no-op unless `force=True` or `verify=True`
  was requested but no `disagreements.json` exists. See `_load_cached` in
  `pipeline.py`.
- Each transcription is its own `v<n>` directory. New runs never overwrite old
  ones; `latest_version()` is what other code reads.
- `provenance.json` is the source of truth on disk. `library.sqlite` is a
  derived index — `reindex()` rebuilds it from provenance files.
- Blocks carry `verify` status (`unverified` | `verified` | `disagreement` |
  `flagged`). Tools that filter by `only_verified=True` rely on this.
- Output paths use absolute filesystem paths. The MCP client renders images
  by reading the path from disk; the server doesn't stream bytes.
- `out/` is `./out` relative to CWD unless `DOCSMCP_OUT` is set. The cwd here
  is wherever the MCP client launches the server — not the project root.

## Environment variables

| Var | Default | Effect |
|---|---|---|
| `DOCSMCP_OUT` | `./out` | Where transcriptions, images, and `library.sqlite` live. |
| `DOCSMCP_EMBED_MODEL` | `mxbai-large` | Embedding model alias (`bge-small`, `bge-large` also work). |
| `DOCSMCP_RERANKER` | `BAAI/bge-reranker-v2-m3` | HF repo for the cross-encoder. |
| `DOCSMCP_VLM_MODEL` | `mlx-community/Qwen3-VL-4B-Instruct-4bit` | MLX VLM for `verify_*` tools. |

## Gotchas

- `server.py` (~1000 lines) and `index/fts.py` (~1700 lines) are the two
  oversized files. Prefer adding new logic in a focused submodule and exposing
  it through a thin `@mcp.tool` wrapper rather than growing either further.
- `transcribe` (the MCP tool) and the internal function in `pipeline.py` share
  a name; `server.py` imports the internal one as `_transcribe`. The public
  `transcribe` tool is a deprecated alias for `ingest_file`. Use `ingest_file`
  in new tool docs.
- Marker (`verify=True`) doesn't emit bboxes, so `get_block_image` only works
  on Docling-sourced blocks. Don't promise crops for `engine == "marker"`.
- The metadata pipeline runs in this order: heuristic → CrossRef (if DOI
  found) → explicit args. CrossRef wins over heuristics; explicit args win
  over everything. See `pipeline.transcribe` ~lines 200–230.
- `_safe_json` in `index/fts.py` exists because corrupted columns used to
  crash list endpoints. Keep using it for any new JSON column reads.
- `verify_visual` reloads the VLM per call. It's slow. Don't add it to any
  default code path.

## Style notes specific to this repo

- Dataclasses + `asdict` everywhere; no Pydantic. New schema goes in
  `store/schema.py`.
- Logging uses the stdlib `logging` module under the `docsmcp.*` namespace —
  not `print`. `pipeline.py` still has one `print(...)` for a non-fatal warn;
  leaving it for now, but don't add more.
- Tool docstrings are user-facing (Claude Code shows them). Keep the first
  line short and imperative; put parameter detail in `Args:`.
