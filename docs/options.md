# Options

Every flag `pdf2md convert` takes, and which ones a given PDF wants.
The [README](../README.md) has the five commands most runs need; this is
the rest.

## Options reference

Grouped by what they touch. All are flags to `convert`.

**Speed / defaults-off**
- `--no-formula` — skip equation→LaTeX (much faster; equations become image crops)
- `--no-scripts` — skip inline sub/superscript recovery
- `--no-digitize` — skip born-digital vector-chart data recovery
- `--no-figure-ocr` — skip the model-free upright re-OCR of scanned figures
- `--no-word-split` — skip English word-resplit of OCR'd prose (use for non-English scans)
- `--no-page-images` — skip per-page verification rasters for scanned pages (saves disk)
- `--page-images-all` — capture a full-page image for every page (page-faithful capture: any
  answer can be checked against the source image; ~100–300 KB disk per page)

**Scanned-document OCR** (vision flags need the `describe` extra + an endpoint)
- `--tables-only` — hunting one table: skip formula enrichment, chart digitization and
  figure OCR. Table cells, audits and crops are unaffected. Measured at 27% off a 28-page
  scan; the parse dominates, so it trims rather than transforms
- `--engine mineru` — use MinerU's native structure for scans and difficult tables/equations
- `--engine auto` — Docling for a born-digital document, MinerU for a scan where its
  executable is configured and installed. The decision reads the PDF's own text layer
  through `GlyphIndex`, so a digitised scan carrying someone else's OCR is still a scan;
  a plain text-presence test calls a 99-page overlay scan 0% scanned. Never fails a run
  over an absent optional engine: it warns and uses Docling. In directory mode the
  decision is made per document, so the batch does not share one engine.
- `--engine marker` — use Marker, the best-reading option; needs `--marker-executable` and a
  running inference server (see Installation)
- `--mineru-executable PATH` — MinerU CLI in its separate environment
- `--table-ocr-executable PATH` — compare numeric table cells with Tesseract; never replaces values
- `--table-reference CSV` — compare normalized cells with a semantic external reference
- `--grobid-url URL` — enrich title/authors/abstract/DOI and parse all reference strings via a
  running GROBID service (e.g. `http://localhost:8070`); fills gaps in the heuristic metadata,
  writes the raw TEI under `data/`, and degrades to heuristics with a warning if unreachable
- `--metadata-online` — resolve a locally extracted DOI to CSL-JSON, retain the raw registry
  record, and merge structured citation fields with local evidence
- `--ocr-page-vlm` — transcribe each scanned page as one Markdown block (one call/page)
- `--force-ocr` — ignore a bad embedded text layer and re-OCR page images
- `--no-deskew` — skip conservative fine-deskewing of textless pages before MinerU
- `--transcribe` — re-transcribe image-backed equations with local math-OCR (Surya; `transcribe` extra)
- `--render-check` — render each image-backed equation's LaTeX and compare ink layout against its
  source crop as review evidence (`eqrender` extra; targets scanned pages where no text layer exists)

**Figures & charts**
- `--digitize-vlm` — also estimate data from raster/scanned plots (approximate, low confidence)
- `--digitize-consensus N` — sample `--digitize-vlm` N times per figure and keep the per-bin
  median curve, scaling confidence by across-draw dispersion (one extra model call per vote)
- `--figure-labels` — vision read of a figure's printed labels/legend/axis titles
- `--figure-svg` — export born-digital figures as lossless SVG (needs poppler)
- `--describe` — vision description beneath each figure/table/equation crop

**Vision-model selection**
- `--vlm-model NAME` — model for `--describe` figures and plot reasoning (default `qwen3-vl:8b`)
- `--vlm-ocr-model NAME` — OCR-tuned model for dense tables/equations (e.g. `glm-ocr`)
- `--ocr-consensus N` — re-read each figure label N times, flag numeric disagreements
- `--config FILE.toml` — set `vlm_base_url`, `vlm_api_key`, timeouts, and any other config field

**General**
- `--engine docling|mineru` — parser backend (default `docling`)
- `--out DIR` / `-o` — output root (default `./out`, or `PDF2MD_OUT`)
- `--passage-max-tokens N`: cap each contextualized retrieval passage (default 512)
- `--passage-tokenizer lexical|hf:MODEL_OR_PATH`: use the offline deterministic
  counter or the exact Hugging Face tokenizer for the downstream embedding model.
  A model name may download tokenizer files on first use; a local path stays offline.
- `--force` / `-f` — re-convert even if cached (new `v<n>`, never overwrites)
- `--verbose` / `-v`

Other subcommands: `pdf2md doctor [--probe-vlm]`,
`pdf2md list [--out DIR]`,
`pdf2md enrich <document> [--equations] [--charts] [--descriptions] [--dry-run]`,
`pdf2md coverage <pdf> [--out DIR]`,
`pdf2md compare-runs <before-version> <after-version> [--json]`,
`pdf2md review-tables <version-dir>`,
`pdf2md prune --keep N`,
`pdf2md version`, `pdf2md models pull [--local-dir DIR]` (offline/reproducible
model snapshot).

## Which options for which PDF

Start from the row that matches your document. Most feature flags can be
combined, but choose one scan parser path: MinerU preserves element structure;
`--ocr-page-vlm` replaces each page with one Markdown block. Configuration
validation rejects using both together.

| Your PDF | Command | Why |
|---|---|---|
| Born-digital paper (the common case) | `convert paper.pdf` | Defaults already do LaTeX, scripts, and chart digitization. |
| Large book / equation-light doc, want speed | `convert book.pdf --no-formula` | Skips the slow equation→LaTeX pass (10–60× faster); equations stay as cropped images. Add `--no-scripts` to shave a little more. |
| Scanned document (best structured quality) | `convert scan.pdf --engine mineru --mineru-executable env/mineru/bin/mineru` | MinerU won the labelled scan, table, and equation cases. It runs in a separate environment; pdf2md re-renders the source crops and rejects unverified chart data. |
| Scanned document (whole-page Markdown) | `convert scan.pdf --ocr-page-vlm --vlm-ocr-model glm-ocr:q8_0` | Reads prose and equations accurately through an endpoint, but stores each page as one Markdown block rather than separate table/equation elements. |
| Scanned document (no endpoint / offline) | `convert scan.pdf` | Falls back to built-in RapidOCR + word-resplit. Good enough for clean scans. |
| PDF whose own text layer is bad OCR (`?3astman`) | `convert old.pdf --force-ocr --ocr-page-vlm --vlm-ocr-model glm-ocr:q8_0` | Ignores the poisoned text layer and re-OCRs the page images. |
| Scanned **non-English** doc | add `--no-word-split` | The English word-resplitter is wrong for other languages. |
| Charts you want as data (born-digital) | `convert paper.pdf` (already on) | Vector charts ship a CSV + matplotlib repro script. Add `--digitize-vlm` to also estimate raster/scanned plots (approximate). |
| Figures whose printed labels matter | add `--figure-labels` | Vision read of axis titles, legends, peak labels. Needs endpoint. |
| Vector diagrams/schemes you want lossless | add `--figure-svg` | Exports SVG beside each born-digital figure's PNG. Needs poppler. |
| Want AI descriptions under each crop | add `--describe` | Labelled vision description; the crop stays authoritative. A bigger model (`--vlm-model qwen3-vl:32b`) reads embedded text better. |

Model tips for the vision paths: use an **OCR-tuned** model (`glm-ocr`) for
`--ocr-page-vlm` and dense-table crops — it is faster and more exact
than a general VLM (which can take minutes per page). Use a **general** VLM
(`qwen3-vl`) for figure descriptions and plot reasoning. `--vlm-ocr-model` lets you
set the OCR model for tables/equations while `--vlm-model` handles figures.

## Recipes

Copy-paste starting points for the common cases.

```bash
# Born-digital journal paper — nothing to configure
uv run pdf2md convert paper.pdf

# Large / equation-light book, want speed (equations stay as image crops)
uv run pdf2md convert book.pdf --no-formula --no-scripts

# Scanned book or textbook, best structured quality (MinerU stays in its own environment)
uv run pdf2md convert scan.pdf --engine mineru --mineru-executable env/mineru/bin/mineru

# Best reading quality, including inline mathematics (Marker, needs the server above)
uv run pdf2md convert paper.pdf --engine marker --marker-executable env/marker/bin/marker_single

# Endpoint-only alternative: accurate page Markdown, but no separate equation/table blocks
uv run pdf2md convert scan.pdf --ocr-page-vlm --vlm-ocr-model glm-ocr:q8_0

# Same, but the PDF carries a *bad* embedded OCR text layer you want ignored
uv run pdf2md convert old-report.pdf --force-ocr --ocr-page-vlm --vlm-ocr-model glm-ocr:q8_0

# Scanned, no endpoint available — falls back to built-in RapidOCR
uv run pdf2md convert scan.pdf

# Figure-heavy paper: extract chart data + printed labels, describe the rest
uv run pdf2md convert figures.pdf --figure-labels --describe --vlm-model qwen3-vl:32b

# Batch a directory (one bad PDF never aborts the run)
uv run pdf2md convert ~/papers --out ~/library
```

A long scanned run (hundreds of pages) is resumable. Page transcriptions are cached
by model, endpoint, prompt, generation settings, and page image, so re-running after
an interruption picks up where it stopped without reusing an incompatible read. Equation
transcriptions, chart reads, figure labels, and crop descriptions use the same region-level
rule. Each successful model result is written atomically when it finishes. Run
it in the background. Progress is written to stderr by default: pipeline stages include
elapsed time, while MinerU and table verification include completed, total, remaining,
and an ETA once enough work has finished to estimate one. Docling exposes no per-page
counter during its main parse, so pdf2md prints the source page count and elapsed-time
heartbeats until that call returns. `--verbose` also includes the underlying engine
diagnostics.

### Add enrichment after conversion

A completed bundle can receive optional model-backed evidence without running its layout
engine again:

```bash
# Make a fast, useful base bundle for a large book
pdf2md convert book.pdf --no-formula

# Add one or more expensive stages later
pdf2md enrich book.pdf --equations --dry-run
pdf2md enrich book.pdf --equations
pdf2md enrich out/book-a1b2c3d4/v1 --charts --descriptions
pdf2md enrich paper.pdf --metadata
```

`enrich` accepts the original PDF, its document directory, or a completed `v<n>` directory.
When given a PDF outside the default output root, pass the same `--out` used for conversion.
It prints the page count and stage-specific work before starting: image-backed equations and
existing transcriptions, figures with accepted data and remaining model candidates, and eligible
description crops with existing descriptions. Add `--dry-run` to stop after this report. Dry runs
do not create a version or cache file. Large documents also get a warning that model-backed work
may take hours.

Every enrichment run writes a new `v<n>` and leaves its source version unchanged. The new
provenance names the source version, source-provenance hash, base-state hash, selected stages,
effective configuration, model settings, prompt/cache schema, and pdf2md implementation hash.
If an optional model or client is unavailable, the command fails before changing the completed
source bundle. A partial target has no `provenance.json`, so it never counts as complete; the
next run safely reuses its completed region-cache entries.

Endpoint failures after work starts produce a usable version marked `PARTIAL ENRICHMENT` rather
than hiding the missing evidence. That version remains available for reading and comparison, but
it does not satisfy the run cache. After fixing the endpoint, repeat the same command. pdf2md
creates a new version, reuses each successful region result, and calls the model only for cache
misses. The generated README and provenance retain the exact failed-call count.
