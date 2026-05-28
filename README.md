# docsmcp

MCP server for transcribing PDFs/scans to markdown, then chatting with them: hybrid search across a library, page-precise citations, and on-demand page/region images. Designed to be used from Claude Code (or any MCP client).

## What it does

1. **Transcribe**: PDF/scan → markdown with per-block provenance (page, bbox, engine, confidence). Uses Docling with Apple Vision OCR + formula enrichment; optional Marker as a verification pass.
2. **Chunk + embed**: section-aware sliding window → mxbai-embed-large-v1 embeddings → sqlite-vec.
3. **Search**: FTS5 + vector hybrid with reciprocal-rank fusion + bge-reranker-v2-m3 cross-encoder.
4. **Cite**: every result carries `doc_id`, page range, bbox, chunk_id, and `verify` status. `get_page_image` and `get_block_image` produce PNG crops on demand.

## Quickstart

```bash
uv sync
# First run downloads ~1.2 GB of models (embedder + reranker), cached in ~/.cache/huggingface
```

Install as an MCP server in Claude Code:

```bash
claude mcp add docsmcp -- uv --project /Users/charlie/projects/docsmcp run docsmcp
```

Or by hand in `~/.claude.json`:

```json
{
  "mcpServers": {
    "docsmcp": {
      "command": "uv",
      "args": ["--project", "/Users/charlie/projects/docsmcp", "run", "docsmcp"]
    }
  }
}
```

## Tools (13)

### Ingestion
| Tool | Purpose |
|---|---|
| `transcribe(path, profile, kind, tags, ...)` | Transcribe + chunk + embed + index one file. Profiles: `fast`, `balanced`, `max_accuracy`. |
| `ingest_dir(path, kind, pattern, ...)` | Recursively ingest a directory (`*.pdf` by default). |
| `triage(path)` | Fast (no-OCR) per-page classification: born_digital / mixed / scanned / empty. |

### Search & retrieval
| Tool | Purpose |
|---|---|
| `search_chunks(query, top_k, kind?, mode?, rerank?, only_verified?)` | Hybrid passage search. Use for "what page discusses X?" |
| `search_docs(query, top_k, kind?, rerank?)` | Doc-aggregated search. Use for "which papers talk about X?" |
| `get_chunk(chunk_id)` | Full chunk with prev/next links and source metadata. |
| `cite(block_id)` | Resolve a block_id to page + bbox + source path. |
| `get_page(doc_id, page)` | All blocks on one page in reading order. |
| `get_page_image(doc_id, page, dpi)` | Render a page as PNG (cached). |
| `get_block_image(block_id, dpi, padding_pts)` | Crop a single block from the source PDF as PNG. |

### Library mgmt
| Tool | Purpose |
|---|---|
| `list_library(kind?)` | List all transcribed docs, optionally filtered by kind. |
| `set_metadata(doc_id, title, authors, year, kind, tags)` | Override or set metadata (heuristic extraction is best-effort). |
| `reindex()` | Rebuild the index from on-disk provenance files. |

## Typical use from a Claude Code session

```
You: ingest_dir /Users/me/papers --kind paper
[ingests every PDF under that dir]

You: which papers talk about superconductivity in copper oxides?
Claude: [calls search_docs("superconductivity copper oxides")]
  → ranked doc list with best matching chunks + page numbers

You: show me page 14 of the Wang 2024 paper
Claude: [calls get_page("<doc_id>", 14)] + [calls get_page_image(...)]

You: that equation on page 14 looks weird, can you check it?
Claude: [calls search_chunks(..., only_verified=False) + get_block_image(...)]
  → visual side-by-side of what the markdown says vs the actual PDF
```

## Storage layout

```
out/
  library.sqlite              # docs + blocks + chunks + FTS5 + vec_chunks
  {doc_hash[:16]}/v{n}/
    document.md               # primary transcription
    provenance.json           # blocks with page/bbox/engine/verify
    document.alt.md           # Marker output (when verify=True)
    disagreements.json        # classified cross-engine diff (when verify=True)
    pages/p0001@200dpi.png    # rendered page images (cached)
    pages/crops/p0002_<id>@220dpi.png   # block crops (cached)
```

Override with `DOCSMCP_OUT=/abs/path`. Swap embeddings with `DOCSMCP_EMBED_MODEL=bge-small` (or `bge-large`). Swap reranker with `DOCSMCP_RERANKER=<hf_repo>`.

## Profiles

| Profile | Use when | Cost |
|---|---|---|
| `fast` | Born-digital PDF, just need text | ~1 s/page |
| `balanced` (default) | Mixed, includes formula/code enrichment | ~30 s/page on equation-heavy pages |
| `max_accuracy` | Scanned textbook, must catch everything | 3× scale, page images generated |

## Known limits

- **Metadata heuristics are best-effort** — common false positives (author = a multi-capitalized phrase, year = first 4-digit number found). Fix per-doc with `set_metadata()`.
- **Cross-encoder reranker is CPU-only** in the default sentence-transformers path. Adds ~1–2 s on a top-40 candidate set.
- **Marker doesn't emit bboxes**, so `get_block_image` only works on Docling-sourced blocks.
- **Disagreement detection** (when `verify=True`) surfaces real flags + some noise — review the alt.md side-by-side rather than treating flags as gospel.

## Dependencies

- `fastmcp` — MCP server
- `docling` + `ocrmac` — primary OCR/transcription (Apple Vision on darwin)
- `marker-pdf` — secondary engine for verification
- `pymupdf` — page rendering + cropping + triage
- `sentence-transformers` — embeddings + reranker
- `sqlite-vec` — vector store colocated with FTS5
