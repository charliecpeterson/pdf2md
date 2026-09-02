# pdf2md

Auditable PDF → markdown converter. Turns academic PDFs, bookmarked books, and
scanned documents into faithful markdown: text and tables as markdown, equations
as LaTeX, born-digital charts as their extracted data, and anything that can't be
represented as text (figures, photos, complex diagrams) cropped to an image and
referenced. Nothing is silently dropped: anything the tool can't represent emits a
visible marker, and every document reports how much of it is text vs.
image-authoritative.

Naive full-document extraction can corrupt a sentence and still produce fluent prose.
In one two-column paper, `pdftotext` stopped after "spin-orbit interaction free" and
interleaved a separate Dirac-Fock sentence into the surrounding discussion. pdf2md
preserved the operative continuation, "Wood-Boring Hartree-Fock level of theory."
A second paper produced the same kind of near miss around a download watermark. These
errors can reverse the result of a citation check while looking plausible to the reader.

[Docling](https://github.com/docling-project/docling) is the default parser.
[MinerU](https://github.com/opendatalab/mineru) is the measured high-accuracy
option for scans and difficult tables or equations. pdf2md supplies the common
document model, logical-section splitting, bibliographic front matter, source
crops, born-digital chart digitization, and per-document coverage audit.

Checking a claim in a converted paper:

```bash
pdf2md find out/paper-a1b2c3d4/v1 "systematic convergence"
```

The result includes the source page, section, authority, review status, and a
link to the stored PDF at that page. Search an output library instead of one
version to check the phrase across a corpus.

## Design

pdf2md treats conversion as an evidence-preserving transformation, not as a way
to manufacture one clean-looking Markdown file. The exact source PDF remains the
audit authority. The generated bundle is the normal reading and agent interface.

The design follows five rules:

1. Every detected element gets a disposition: structured, image-backed,
   source-backed, flagged, or failed. An uncertain extraction stays visible.
2. Deterministic evidence wins when the PDF provides it. Embedded text, glyph
   coordinates, and vector paths take precedence over another model reading.
3. Expensive or lossy fallbacks are targeted. A difficult region may get a second
   OCR or vision pass; the whole document does not run through every parser.
4. Structured values are emitted only when the relevant accuracy gate passes.
   Otherwise the source crop remains authoritative and the candidate stays labelled.
5. Every derived artifact carries enough page, bounding-box, model, configuration,
   and source-hash information to reproduce or challenge it.

```text
source PDF
  -> Docling or MinerU
  -> engine-neutral document model
  -> deterministic repair and verification
  -> targeted OCR, vision, or external-reference evidence
  -> representation selection
  -> versioned Markdown + data + assets + provenance bundle
```

Markdown is the main reading surface, but it is deliberately not the complete
artifact. `manifest.json`, `passages.jsonl`, and `chunks.jsonl` provide retrieval
and bounded navigation;
`profile.json` and `provenance.json` report quality and lineage; `source.pdf` and
the source crops make every uncertain claim inspectable.

### Approach map

| Problem | Current approach | Status and boundary |
|---|---|---|
| General PDF structure | Docling layout, reading order, tables, formulas, and bounding boxes | Default production path for born-digital documents. |
| Scans and difficult tables/equations | MinerU native structure through a separate CLI environment | Production option selected by the labelled bake-off; not run as a blanket second parser. |
| Page rendering and exact PDF evidence | PDFium through pypdfium2 for glyphs, page rasters, crops, outlines, and vector objects | Production evidence layer, independent of the parser adapter. |
| Clean scanned prose | RapidOCR followed by conservative punctuation repair and English word re-splitting | Default offline fallback. Word splitting is disabled for non-English scans. |
| Whole-page OCR | OCR-focused VLM through an OpenAI-compatible endpoint | Opt-in. It can improve page text but collapses table, equation, and caption structure into one Markdown block. |
| Equations | Docling LaTeX checked against the embedded text layer; suspect results become image-backed | Production. Surya re-transcription and Matplotlib render-back comparison are opt-in evidence. |
| Scans carrying an OCR text layer | Detected from a full-page image plus invisible (render-mode-3) text, and treated as a scan | Production. This is the one case where a text layer exists but is not the page's own words, so every glyph check would otherwise confirm the engine's errors instead of catching them. |
| Reading order | Page columns recovered from block geometry, plus the document's own numbering where a page carries an unbroken run of ordinals | Production. The rest of the audit is order-insensitive by design, so an interleaved two-column page conserves every word and number and still reads as nonsense. The numbering path is proof rather than inference but only covers bibliographies the engine emits one entry per block; the geometric path covers the rest. |
| Prose against the text layer | Per-block word recall on a script-split, hyphen-joined reading, with accent damage separated from missing words | Production. Missing words are an action beside the block; lost diacritics are recorded without burying a bibliography in markers. |
| Born-digital tables | Engine grid checked cell-by-cell against PDF glyphs, plus a row-level audit that projects the page's own ink into rows and accounts for every value in it | Production verification. A dropped, merged, or shifted row is named in the Markdown, in every derived artifact, and in `review.md`; the glyph-truth reading of the same region ships beside the engine's grid. |
| Scanned numeric tables | Crop-authoritative table artifacts, normalized candidates, optional Tesseract comparison, exact external references, and deterministic review sheets | Production is evidence-first. Reader agreement, scientific relations, and validators never silently replace a value. |
| Experimental table readers | PP-OCRv6/PaddleOCR-VL, projection-derived crops, fixed-font glyph atlases, and row/column recovery | Evaluation-only or separate non-mutating overlays. The measured corpus does not justify automatic OCR value promotion. |
| Born-digital charts | Vector-path geometry converted to CSV and deterministic Matplotlib code | Production for supported line/scatter charts; the source SVG or crop remains beside the data. |
| Raster or scanned charts | Calibrated VLM estimates, optionally sampled to measure dispersion | Opt-in and approximate. Hard plots remain image-authoritative. |
| Figures and diagrams | Source crop, optional lossless SVG, model-free printed-label OCR, and optional VLM description | The visual remains authoritative; labels and descriptions are search aids. |
| Document metadata | Ranked bibliographic fields, inferred paper/book type, semantic section roles, checksum-validated ISBNs, and source-addressed references; optional DOI registry and GROBID enrichment | Production. Verification states report evidence and agreement, never probability. Selected evidence, alternatives, conflicts, and raw external records remain visible. |
| Agent access | Logical sections, token-bounded passages, page-local chunks, document and symbol maps, review queues, and direct source links | Production. General RAG, vector search, and corpus management remain outside pdf2md. |

The project has also ruled out several tempting shortcuts until new evidence
changes the result: running every parser on every page, lowering the 0.99
PP-OCRv6 confirmation threshold, treating OCR votes over shared crop geometry as
independent verification, replacing every OCR crop with a projection crop, and
promoting glyph-atlas choices automatically. New input formats, CrossRef
enrichment, MCP/corpus search, more chart archetypes, and public release automation
are deferred rather than partially implemented.

The remaining evidence-gated candidates are narrower: an opt-in arXiv-source
verifier, a reliable live A/B for raster-chart consensus, periodic reruns of the
pinned gates when parser or OCR models change, a broader assertion-style PDF
corpus, and further independent table-grid work only where a labelled corpus shows
an advantage. The status and measurements for each are kept in
[the accuracy notes](docs/accuracy-improvement-notes.md).

The completed large-document performance, quality-reporting, and retrieval-format
work is recorded in the [quality and ingestion plan](docs/quality-and-ingestion-plan.md).

## Install

```bash
uv sync
uv run pdf2md models pull        # first run downloads Docling's models (~once)
```

Optional extras, only needed for the flags that call them:

```bash
uv sync --extra transcribe       # local math-OCR (Surya) for --transcribe
uv sync --extra describe         # vision-model flags (--describe, --ocr-page-vlm, ...)
brew install poppler             # --figure-svg (pdftocairo)
```

MinerU stays outside the project environment because its model stack conflicts
with Docling's dependencies:

```bash
uv venv env/mineru --python 3.11
uv pip install --python env/mineru/bin/python 'mineru[all]'
```

CUDA formula enrichment also needs the development headers for the selected
Python (`python3-devel` or the versioned equivalent on Linux) because PyTorch/Triton
compiles a small CUDA helper. `pdf2md doctor` reports the exact missing header. Use
`--no-formula` when image-backed equations are acceptable and those headers are not
available.

The vision-model flags talk to any OpenAI-compatible endpoint (ollama, vLLM, LM
Studio, or a remote API). Default is a local ollama at `http://localhost:11434/v1`;
point elsewhere with `vlm_base_url` / `vlm_api_key` in a `--config` TOML.

## Quick start

```bash
uv run pdf2md doctor                         # verify the default install and show optional tools
uv run pdf2md convert paper.pdf              # one PDF  -> out/paper-<id>/v<n>/document.md + assets/
uv run pdf2md convert ~/papers --out ~/lib   # a whole directory (one bad PDF never aborts the batch)
uv run pdf2md list --out ~/lib                # browse completed documents without the originals
uv run pdf2md find ~/lib "quoted phrase"      # page, section, review status, and source link
uv run pdf2md coverage paper.pdf             # coverage report for an already-converted PDF (no re-run)
uv run pdf2md compare-runs out/paper-a1b2c3d4/v1 out/paper-a1b2c3d4/v2 # compare runs
uv run pdf2md review-tables out/paper-a1b2c3d4/v1 # local numeric-cell review sheet
```

`--out` names the library root, not the directory that directly receives
`document.md`. pdf2md adds `<source-name>-<short-id>/v<n>/` beneath it. Use the same root
when inspecting a custom conversion: `pdf2md coverage paper.pdf --out ~/lib`.

Defaults are tuned for a born-digital journal paper and need no flags:
equation→LaTeX, inline sub/superscript recovery, and vector-chart digitization are
all **on**. Re-running the same file with the same effective inputs reuses its completed
version unless `--force` is set or optional model work needs a partial-run retry. Directory
conversion prints cached, converted, and failed totals when the batch completes.

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
- `--engine mineru` — use MinerU's native structure for scans and difficult tables/equations
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

## Development and evaluation

The commands below are for maintaining accuracy gates and reproducing experiments.
They are not needed for normal conversion.

Current design and user-facing behavior live in this README. The focused records
hold the details that would make the main document unreadable:

- [Engine bake-off](docs/engine-bakeoff.md) and
  [bake-off results](docs/bakeoff-results.md)
- [Dense numeric tables](docs/dense-numeric-tables.md)
- [Scan-degradation benchmark](docs/scan-degradation-benchmark.md)
- [Figure-to-text methods](docs/figure-to-text.md)
- [Agent benchmark](docs/agent-benchmark.md)
- [Completed quality, performance, and ingestion plan](docs/quality-and-ingestion-plan.md)
- [Accuracy ideas, implemented experiments, and remaining candidates](docs/accuracy-improvement-notes.md)
- [Archived original project plan and decision log](docs/archive/PROJECT_PLAN.md)

<details>
<summary>Accuracy experiments and evaluator commands</summary>

The development [agent benchmark](docs/agent-benchmark.md) compares answers from
page chunks or stable passages against answers from pinned source-PDF pages. It records
accuracy, citations, opened assets, review flags, and input tokens. A matched
11-question regression gives chunks and passages 11/11 correct answers and 11/11 valid
page citations; passages use 41.8 percent fewer input tokens. Its numeric extension
also scores structured evidence fields, refusal behavior, cross-block joins, derived
arithmetic, and source-crop provenance. The first Fischer run passes 5/6 tasks; the
remaining failure is agent arithmetic rather than extraction.

The [dense-table notes](docs/dense-numeric-tables.md) describe the separate image,
raw OCR, normalized panel, and exact-cell evaluation outputs used for scanned
numeric tables. They also record selective confidence curves across 2,113 natural
cells and a 70-cell PP-OCRv6 score corpus. The current held-out result does not bound
false corrections tightly enough to enable automatic value promotion. A frozen
post-experiment synthesis keeps `automatic_ocr_value_promotion: not_defined`; its
external-source audit finds no independent machine-readable source whose semantic
fields overlap the current extracted tables. Exact references supplied by a user can
still override a semantically mapped cell. Scientific relations, rendering
instability, and geometry remain support or review evidence. A separate
column-geometry comparison recovers the long-configuration GRASP layout through
repeated-row separator consensus and keeps every degraded wrong mapping at zero. A
second source-pinned gate adds weak separators, rule-free Fortran output,
proportional long labels, and nine narrow numeric lanes. It records 238/245 exact
repeated-consensus mappings, zero wrong mappings, and seven refusals after persistent
ruling lines prevent internal word gaps from being mistaken for separators. These
locators remain evaluation-only.

The [controlled scan-degradation benchmark](docs/scan-degradation-benchmark.md)
measures exact cells under independent changes to resolution, blur, rotation,
contrast, and JPEG compression. It also records the Tesseract column-alignment defect
the benchmark exposed and the before/after evidence counts. Its five-family extension
adds fixed-width and proportional text, exponents, superscripts, leading decimals,
curved two-panel scans, binarization, and selected interactions. Across 462 labelled
cell evaluations, the primary produced 398 exact values, one wrong value, and 63
structural refusals; adaptive binarization caused the only emitted error.

Create a deterministic local review sheet to inspect the cells most likely to
matter. It stratifies numeric cells by confidence and source table, links each row to
the source PDF page and untouched crop, and downloads completed labels in the exact
format consumed by the evaluator:

```bash
uv run pdf2md review-tables out/<doc>/v<n> \
  --sample 120 \
  --labels tests/numeric_table_labels.json \
  --output out/reviews/paper.html
```

When Tesseract 5 is installed, `--table-ocr-executable tesseract` writes a second
reading for numeric cells across the document. Reader agreement is evidence, not
external verification, and disagreement never replaces the engine value. The
development evaluator remains available as
`uv run python scripts/eval_numeric_tables.py out --tesseract`.

Scientific consistency is a separate evidence class. The Fischer adapters compare
Table I with term-specific ATSP calculations and recompute normalization plus radial
moments from Table II. Their outputs use `scientific_support`, `disagree`, and
`tool_refused`; they never emit `externally_verified`. See
[dense numeric tables](docs/dense-numeric-tables.md#scientific-support-across-independently-printed-data)
for the frozen results and the controlled v5-to-v6 postprocessing replay.
The evaluation-only exact-relation gate also checks source-declared totals, repeats,
symmetries, and conservation identities with decimal arithmetic:

```bash
uv run python scripts/eval_internal_scientific_checks.py --check
```

It can add support or request review, but never supplies a replacement value.

The scanned-document regression set spans six source-hash-pinned PDFs and keeps
known primary errors separate from source-checked controls. Reproduce its pinned
conversion baseline with:

```bash
uv run python scripts/eval_scanned_numeric_corpus.py --check
```

The current 124-cell set contains 20 primary errors: six Fischer cells, the Slater
`8.0` cell, eight degraded ORNL Fortran-output cells, and five NASA dot-matrix cells.
Its 104 clean controls cover the same documents plus a dense two-lane NIST
spectroscopy table. The Slater error is a three-way case: the source is `8.0`, the
primary reads `8.9`, and Tesseract reads `8.u`. ORNL adds sign, exponent-marker,
digit-to-letter, and digit-substitution errors; NASA adds dot-matrix digit and
zero-to-letter errors.

The non-Fischer natural-error corpus widens this to 33 documents and keeps sampling
roles separate. It contains 2,113 source-checked numeric cells: 2,099 correct primary
values and 14 natural errors across Slater, ORNL, and NASA. Independent-reader
evidence has 1,078 agreements, 182 disagreements, and 853 refusals. Six
scientific-notation values omitted from an orbitals data column, two Dolg tables
retained as HTML, and 431 unsafe NIST auxiliary-reader mappings are reported as
structural outcomes rather than silently counted as correct values. Reproduce the
pinned result with:

```bash
uv run python scripts/eval_natural_numeric_error_corpus.py --check
```

The pooled number is not a prevalence estimate. It combines independently mapped
LaTeX cells, complete extracted-table reviews, targeted syntax coverage, and an
error-enriched scanned slice; the report preserves those roles and per-document rates.

The active-review experiment uses leave-one-document-out weights rather than labels
from the document being ranked. It improves aggregate small-budget error recall, but
does not beat confidence-stratified review on every held-out document and budget, so
the production review sheet remains confidence-stratified. Reproduce that decision
with:

```bash
uv run python scripts/eval_active_review_heldout.py --check
```

A separate factorial gate tests whether OCR reads change across DPI, grayscale versus
adaptive thresholding, deskew, and crop padding. On 14 natural primary errors plus 56
clean controls, off-baseline instability detects 13 errors but also marks 15 controls.
This signal is suitable for review ranking only:

```bash
uv run python scripts/eval_natural_rendering_stability.py compare \
  out/reviews/rendering-stability-natural-errors-v1/run.json --check
```

For targeted PaddleOCR-VL experiments, `scripts/prepare_paddleocr_crops.py`
builds source-linked crops only where row and column geometry can be proved.
`scripts/run_paddleocr_reference.py` preserves each service response, and
`scripts/eval_numeric_tables.py --paddle-cell-run <run.json>` scores exactly one
numeric read per crop. This is an evaluation path, not an automatic resolver.
`scripts/eval_fixed_font_glyphs.py` can then build a document-specific glyph atlas
from cells where the two readers agree and rank same-length candidates by source-image
shape. `--ocrflux-manifest <manifest.json>` accepts hash-pinned OCRFlux table
Markdown with explicit row and column mappings. Gold labels score these experiments
but are never used to build the atlas. Calibration documents can pin a conversion
version so a later exploratory conversion cannot silently change the evaluated grid.
The source-checked disagreement sets in
`tests/glyph_slater_disagreement_labels.json` and
`tests/glyph_pdf_parse_disagreement_labels.json` exercise both candidate ranking and
explicit refusal on other scanned and born-digital fonts. The glyph report also
records leave-one-atlas-cell-out preferences. Those are diagnostics, not confidence:
the labelled Fischer corpus contains a wrong choice that remains stable in every
trial.

`scripts/eval_reader_cascade.py` evaluates a narrower alternative: call a preserved
third reader only when the primary and Tesseract disagree, accept a value only when
the third reader matches exactly one candidate, and otherwise retain the primary as
unresolved. `scripts/prepare_paddleocr_crops.py --reader-disagreements REPORT` writes
only the cell crops that this cascade would request. These remain development tools;
the measured corpus is not yet large enough to enable automatic third-reader
replacement in conversion output.

The non-Fischer follow-up uses 14 human-verified, hash-pinned source boxes rather
than either OCR reader's token geometry. Pinned PP-OCRv6 accepts two correct ORNL
values and no wrong values at the frozen 0.99 threshold, while refusing 12 cells.
The preserved disagreement-only cascade still makes zero corrections because its two
eligible third reads are below threshold; the accepted values occur where Tesseract
refused. Reproduce the evaluation with:

```bash
uv run python scripts/eval_natural_error_third_reader.py --check
```

`scripts/eval_table_keys.py` applies the same refusal-first policy to text-valued
single-panel row keys. It compares whole-table and isolated-key Tesseract reads,
keeps `I`/`l`/`1`/bar glyphs unresolved, and can emit selective crops for a preserved
PaddleOCR-VL trial. This is also an evaluation path; row labels are not rewritten.

`scripts/eval_line_reader.py` extends that benchmark to 231 source-pinned crops from
four documents and typefaces. The fourth document was held out until the 0.99 rule was
frozen. `scripts/run_paddle_line_reader.py` runs the isolated recognizer without
receiving primary or expected labels. PP-OCRv6 confirms 147/231 keys with zero false
confirmations; 84 remain refused. The held-out monospaced table contributes 20/20
correct confirmations. The reader still makes two high-confidence errors elsewhere,
both blocked by the required agreement with the primary extraction.

The same gate is available as an optional, non-mutating post-conversion stage. It
keeps PaddleOCR outside the default environment and writes a separate evidence
overlay:

```bash
pdf2md line-reader prepare out/<doc>/v<n> out/reviews/<doc>-line-reader

# Optional inclusive source-page range for a reproducible table-family run.
pdf2md line-reader prepare out/<doc>/v<n> out/reviews/<doc>-line-reader \
  --page-from 18 --page-to 86

# Run this manifest in the pinned PaddleOCR environment, normally on the CUDA box.
python scripts/run_paddle_line_reader.py \
  out/reviews/<doc>-line-reader/inputs.json \
  out/reviews/<doc>-line-reader/run.json \
  --model PP-OCRv6_medium_rec \
  --device gpu:0

pdf2md line-reader apply out/reviews/<doc>-line-reader \
  --run out/reviews/<doc>-line-reader/run.json
```

`prepare` uses Tesseract only to locate row-key geometry, records source and crop
hashes, and keeps primary values out of `inputs.json`. `apply` accepts only the pinned
model artifacts and package versions, then requires a score of at least 0.99 plus
normalized agreement with the primary. It writes `evidence.jsonl` and `report.json`;
the completed conversion is not changed. Repeated side-by-side panels first use the
exact-count anchor path: the locator proves a distinct boundary gap, finds an
exact-count anchor lane, and uniquely aligns keys in the other panels by vertical
position. If the extracted panels contain different row sets, the fallback treats
each lane independently. It requires increasing numeric keys and a unique match
inside that panel's source bounds. Missing, ambiguous, and nonincreasing keys are
refused. Row identities and values are never copied between panels. The manifest and
report record the locator used and separate unavailable key cells from refusal events.

`scripts/eval_source_row_recovery.py` tests a stricter fallback for grids whose row
keys and values become structurally shifted. It infers a repeated numeric row-key
template from other source blocks and crops each numeric cell from that panel's
pixels. The ordinary path requires the exact source-line count. A one-line fallback
requires at least 85 percent exact key matches, exact keys bracketing the gap, and one
intervening source line inside the same panel bounds. The pinned reader must then read
that inferred line as the missing template key before any shifted row can pass. A
separate pixel-projection locator must also find the complete row sequence and place
every OCR line inside the corresponding ink band. It uses no OCR tokens or
Tesseract-derived column bounds. Repeated panels are first separated by dominant
vertical whitespace; missing or ambiguous gutters are refused. Each row still
requires its own independent source-key read. Within an accepted row, horizontal ink
runs must produce the structural column count and the matching run center must fall
inside the OCR-derived cell box. Data cells also require Tesseract-reader agreement
above the frozen threshold. Edge gaps, ambiguous lines, projection mismatches, and
failed inferred-key reads remain refusals. The script writes a hash-pinned
`rows.jsonl` overlay and never changes the conversion output.

`scripts/eval_source_row_alignment_corpus.py` tests that one-gap rule outside the
radial-table paper. Its source-pinned corpus contains 527 numeric cells in five panels
from three PDFs. The independent projection locator matches all 106 source rows before
the evaluator perturbs only Tesseract's key tokens. Five interior one-gap cases must
restore the original row mapping; edge gaps, two gaps, broken anchors, ambiguous
intervening lines, projection mismatches, and cases below the exact-key threshold must
refuse. Run the frozen comparison with:

```bash
uv run python scripts/eval_source_row_alignment_corpus.py --check
```

`scripts/eval_heldout_key_reader.py` tests recognition on those same source-checked
row keys rather than stopping at geometry. It compares the production Tesseract
word-box crop with a token-free ink envelope inside the known key lane, while keeping
the PP-OCRv6 reader and its 0.99 threshold fixed. The word-box path accepts 103/106
keys, with one wrong read and two refusals. The projection path accepts all 106 keys
across all three PDFs. Used only after the word-box path fails its semantic gate, it
supplies three keys and reaches 106/106. The two GRASP failures contain a neighboring
position digit in the word box; the Slater crop reads the right value below threshold.
Both reader runs, every crop, and the expected outcomes are hash-pinned.

```bash
uv run python scripts/eval_heldout_key_reader.py prepare
python scripts/run_paddle_line_reader.py \
  out/reviews/heldout-key-reader-v1/inputs-reference.json \
  out/reviews/heldout-key-reader-v1/run-reference.json --device gpu:0
python scripts/run_paddle_line_reader.py \
  out/reviews/heldout-key-reader-v1/inputs-projection.json \
  out/reviews/heldout-key-reader-v1/run-projection.json --device gpu:0
uv run python scripts/eval_heldout_key_reader.py compare \
  --reference-run out/reviews/heldout-key-reader-v1/run-reference.json \
  --projection-run out/reviews/heldout-key-reader-v1/run-projection.json --check
```

`scripts/eval_heldout_data_reader.py` applies the same crop differential to 56
visually source-checked data cells from six panels. The set covers long negative
energies, large level values, exact zeros, leading decimals, trailing zeros,
superscript footnotes, scientific notation, semantic placeholders, narrow columns,
and values down to `0.00061`. All 56 primary values are correct. Tesseract reads 51/56
cells semantically; its five errors turn superscript `a` footnotes into trailing
digits. PP-OCRv6 accepts 52/56 existing crops and 51/56 projection crops with no
accepted wrong value. A refusal-only crop fallback accepts 53/56, using projection
once for `-0.02146` and refusing one footnoted cell plus two placeholders.

```bash
uv run python scripts/eval_heldout_data_reader.py prepare
python scripts/run_paddle_line_reader.py \
  out/reviews/heldout-data-reader-v1/inputs-reference.json \
  out/reviews/heldout-data-reader-v1/run-reference.json --device gpu:0
python scripts/run_paddle_line_reader.py \
  out/reviews/heldout-data-reader-v1/inputs-projection.json \
  out/reviews/heldout-data-reader-v1/run-projection.json --device gpu:0
uv run python scripts/eval_heldout_data_reader.py compare \
  --reference-run out/reviews/heldout-data-reader-v1/run-reference.json \
  --projection-run out/reviews/heldout-data-reader-v1/run-projection.json --check
```

This measures clean-cell confirmation, false alerts, and the covered numeric syntax.
It contains no primary error and therefore does not measure correction recall.

`scripts/eval_projection_row_stress.py` freezes the locator's failure boundary on 28
deterministic transformations of three source-pinned panels. Equal-width panel splits
produce 16 exact mappings. Source-pixel gutter detection raises that to 23, including
all five unequal-width cases, with four disagreements and one locator refusal. The
production cross-check turns those five unsafe cases into refusals, for zero accepted
wrong mappings. It covers skew and deskew, crop shifts, salt-and-pepper noise,
curvature, unequal panel widths, blur, rules, and a false footer. Run it with:

```bash
uv run python scripts/eval_projection_row_stress.py --check
```

`scripts/eval_projection_panel_corpus.py` compares the same gutter detector with
7,886 hash-pinned key-cell centers across the Fischer radial-table corpus. All 130
tables with an independent key-cell reference agree; one additional table has no
prepared reference cells and is reported separately rather than counted as a pass.

```bash
uv run python scripts/eval_projection_panel_corpus.py --check
```

`scripts/eval_projection_column_corpus.py` compares per-row source-pixel ink runs
with 1,089 cell boxes created before the independent column locator. All 1,089 run
centers land inside their assigned cell boxes across seven reference-bearing Fischer
panels; five panels without prepared cell references remain explicit skips.

```bash
uv run python scripts/eval_projection_column_corpus.py --check
```

`scripts/eval_projection_crops.py` then uses those independent row bands and ink runs
to make an alternate crop for each of the same 1,089 cells. The alternate crop is not
a replacement: four of 41 reviewed cells fall below the frozen reader threshold. As
a fallback after an OCR-box crop refusal, it retains 41/41 reviewed labels and raises
accepted recovery values from 441 to 541. The corpus pins both reader runs, both crop
hashes, and the two source-reviewed divergences it uncovered.

```bash
uv run python scripts/eval_projection_crops.py prepare
python scripts/run_paddle_line_reader.py \
  out/reviews/fischer-projection-crops-v1/inputs.json \
  out/reviews/fischer-projection-crops-v1/run.json --device gpu:0
uv run python scripts/eval_projection_crops.py compare \
  out/reviews/fischer-projection-crops-v1/run.json --check
```

Run the reader command in the pinned GPU environment recorded in the manifest; the
project's ordinary development environment does not include PaddleOCR.

The source-row producer can now apply that crop as a refusal-only fallback. Prepare
emits separate hash-pinned input manifests for the original and projection crops.
After both runs are available, apply them with:

```bash
uv run python scripts/eval_source_row_recovery.py apply \
  out/reviews/fischer-source-row-recovery-v3 \
  --run out/reviews/fischer-source-row-recovery-v2/run.json \
  --projection-run out/reviews/fischer-projection-crops-v1/run.json \
  --labels tests/fischer_source_row_recovery_labels.json
uv run python scripts/eval_source_row_fallback.py --check
```

The integrated corpus preserves all 610 prior candidates unchanged and adds 137,
raising the overlay to 747 candidates. Each accepted cell names `reference` or
`projection` as its reader path; both readings and both crop hashes remain available
for audit.

This benchmark measures row-to-cell mapping safety. It does not estimate the natural
frequency of OCR failures or validate the recognized value inside each mapped cell.

</details>

For an agent, start with `manifest.json`, `metadata.json`, and `outline.json`, then search the
contextualized text in `passages.jsonl`. Use the matching source regions or page-local
`chunks.jsonl` record for citation. Open only the named Markdown and assets. Open `source.pdf`
when a review flag or the task itself requires checking the audit source. On the
pinned 11-question corpus, this path answered 11/11 questions with valid page
citations, versus 10/11 from oracle-selected PDF pages, while using 59.4 percent
fewer input tokens.

From Python:

```python
from pdf2md.pipeline import convert_file
result = convert_file("paper.pdf")
print(result.coverage.accounted_for, result.coverage.needs_review, result.md_files)
```

## Output

```
out/<source-name>-<doc_id[:8]>/
  source.pdf            # exact, hash-verified source shared by every derived version
  v<n>/
    document.md         # paper: one file
    00_front.md ...     # book: front matter, Part openers, chapters, and back matter
    index.md            # book: shallow file-level contents tree
    README.md           # human run summary: contents, quality scorecard, conversion work
    manifest.json       # compact navigation, representations, and review links
    metadata.json       # document kind, fields, semantic sections, references, verification
    chunks.jsonl        # bounded text units with source and asset pointers
    passages.jsonl      # stable retrieval records with contextualized text
    passages.schema.json # JSON Schema for each passage record
    outline.json        # hierarchy, file/passage ranges, review and source map
    symbols.json        # source-quoted, section-local technical symbol definitions
    profile.json        # machine-readable inventory and quality signals
    review.md           # human queue: likely defects before valid image dependence
    review.json         # the same queue as structured records and exact counts
    base-state.json     # engine-neutral parse state for parser-free enrichment
    assets/<id>_p<n>.png
    data/<id>_p<n>.csv  # accepted chart series
    data/doi-metadata.csl.json # optional raw DOI registry response
    data/tables/<block>.md   # table transcription candidate, with its own audit header
    data/tables/<block>.glyph.md # the same region read out of the PDF's glyph layer
    data/tables/<block>.csv  # raw cell grid
    data/tables/<block>.json # authority, crop, audit findings, and lineage
    data/tables/<block>.cells.jsonl # per-cell reader and reference evidence
    data/tables/page_<n>_panels.csv  # stitched long-form repeated panels, when detected
    data/tables/page_<n>_panels.json # stitched rows and review checks
    code/<id>_p<n>.py   # deterministic chart reproduction
    provenance.json     # full blocks, bboxes, coverage, and lineage
```

- Every table artifact opens with its own provenance and audit header. A file under
  `data/tables/` is read away from `document.md`, and an unmarked grid there would
  present as a standalone source; findings on the table's own block reproduce in full
  and findings elsewhere on its page become a pointer into `review.md`. Each table also
  gets a crop of its printed region under `assets/`, so the source can be checked
  without opening the PDF.
- `<block>.glyph.md` is the table region read straight out of the glyph layer, in the
  engine's columns: measured rows against a modelled grid. It is never the emitted
  table, and exists so a suspect row can be diffed rather than trusted.
- `doc_id` is the SHA-256 of the source bytes. A completed version is reused only when
  its run fingerprint also matches the effective configuration, pdf2md implementation,
  engine identity, dependency versions, model identifiers, and prompt/cache schema.
  `--force` always creates a new version; runs never overwrite completed output.
- The readable source-name prefix is for navigation; the `doc_id` suffix prevents files
  with the same name or changed contents from sharing a version tree. It is normally eight
  characters and extends automatically if that name already belongs to different content.
  Existing hash-only output directories remain valid and are reused when found.
- Directory conversion excludes its configured output tree, so a repeated
  `pdf2md convert .` does not ingest the stored `source.pdf` copies. `pdf2md list`
  finds verified document roots recursively and reports their latest completed content.
- `source.pdf` is written atomically and verified against `doc_id`. Versions refer
  to this single audit copy rather than duplicating the source.
- Book output expands chapter bookmarks beneath Part-like containers while leaving
  local section, appendix-subsection, and index-letter bookmarks inside their parent
  file. Out-of-order bookmark destinations are restored to source-page order. If a Part
  has no chapter bookmarks, two or more numbered chapter headings provide the fallback.
  The root `index.md` lists files only; each content file links its own detailed headings.
- `pdf2md prune` only treats a directory as a document when its name and stored
  `source.pdf` agree on the content identity. Unrelated `v1`/`v2` directories are ignored.
- `manifest.json` is the machine entry point. It points to the Markdown, source,
  assets, review targets, profile, base state, and full provenance without duplicating their
  contents.
- `metadata.json` carries the selected bibliographic fields, inferred document kind,
  semantic roles for paper and book sections, and one record per extracted reference.
  Numbered reference sections report sequence gaps; continuation blocks retain every source
  block and page. DOI matches and GROBID reads can corroborate an entry, while disagreements
  remain visible as conflicts. Verification labels describe traceable evidence and extractor
  agreement, not calibrated probabilities.
- `manifest.json` also carries the selected bibliographic fields and their ranked
  evidence. Title candidates retain source pages and block IDs where available;
  alternatives record ranking points, quality labels, and penalties such as probable
  glyph fragmentation. Rejected generic/generated titles and placeholder authors stay
  visible. Scores order candidates and are not calibrated probabilities.
- `chunks.jsonl` groups consecutive blocks from one section and source page into
  records of at most 6,000 characters. Each record names its Markdown file,
  exact source page, block IDs, assets, and review dispositions. `needs_review`
  means an action is required; valid image dependence is recorded separately.
- `passages.jsonl` is the stable retrieval and embedding interface. Its IDs derive
  from source block IDs rather than sequence position, so editing one block does not
  rename every later passage. Records carry separate display and contextualized text,
  content hashes, full breadcrumbs, source regions, neighbors, authority, review state,
  typed assets, and tokenizer identity, count, and limit. The limit applies after
  document and section context is added. Prose splits at paragraph or sentence
  boundaries, list and code lines stay intact when they fit, and table continuations
  repeat their caption and column header. Equation passages include a nearby explanatory
  sentence; figure passages include their caption and an explicit referring sentence
  when found. Retrieval context also names the semantic section role, such as `abstract`,
  `methods`, `results`, `conclusions`, or `references`. `passages.schema.json` is the
  bundled copy of the
  [published schema](src/pdf2md/passages-v2.schema.json).

  The default `lexical` tokenizer is deterministic and requires no model files. To size
  passages for a real embedding index, select that model's tokenizer, for example:

  ```bash
  pdf2md convert paper.pdf \
    --passage-tokenizer hf:sentence-transformers/all-MiniLM-L6-v2 \
    --passage-max-tokens 256
  ```

  The Hugging Face model name or local path is stored in every passage. Use the same
  tokenizer and limit when indexing; pdf2md counts special tokens in the recorded limit
  and rejects a configured limit above the tokenizer's finite `model_max_length`.
- `outline.json` is the deterministic navigation map. It derives from the same section
  tree and passage records as the Markdown, and reports section and file page ranges,
  passage ranges, block and passage counts by content type, review hotspots, named
  bibliography/glossary/index locations, and every source-dependent passage. Paths and
  source pages are direct bundle references rather than regenerated summaries.
- `symbols.json` is a conservative, section-local index of explicitly defined technical
  symbols. Each entry quotes the defining source sentence and links its passage, source
  region, and same-section occurrences. The extractor declines unintroduced notation
  and keeps overloaded symbols separate by section; it does not assign inferred global
  meanings.
- `review.md` and `review.json` separate likely defects from valid source dependence.
  Suspect extractions, illegible prose, and missing representations appear first.
  Intentional image-backed equations from `--no-formula` remain searchable without
  making the whole document appear defective.
- Accepted chart values and plotting code live in `data/` and `code/`. Markdown
  links to both while keeping the source figure beside them.
- Figure cleanup can join explicitly captioned multipanel and continued figures, and
  can expand a single graphical-abstract image to include tightly adjacent visual
  text/equation components. These rules require layout evidence and preserve the
  component text blocks for search and provenance.
- Scanned tables keep the crop as the authoritative record. Their structured OCR
  remains searchable as explicitly labelled Markdown, CSV, and JSON candidates.
  Repeated side-by-side panels, unequal-width radial panels, vertical Table I records,
  and headerless continuations get typed long-form CSV. Interleaved Table I lanes are
  parsed independently, and separate or inline scalar properties use an explicit
  `value` column. Atomic number constrains the parsed element symbol. Raw OCR stays
  beside parsed values. Normalized rows include both readers, a conservative
  `best_value`, confidence, the resolution rule, validator preference, and refusal
  details. Continuity and format validators are diagnostic only; they do not rewrite
  the primary value. Reader agreement remains distinct from external verification.
- Unchanged files under `assets/` are hard-linked across completed versions. Each
  version keeps normal relative paths, and pruning an older version does not remove
  bytes still referenced by a newer one. Treat generated assets as immutable: an
  in-place edit to a linked file is visible from every version sharing that file.
- Front-matter carries `format_version`, bibliographic metadata, and the engine +
  model versions that produced the file.
- `provenance.json` records the effective configuration and the complete inputs used
  to compute the run fingerprint. Base runs record `derivation.kind: base`; enrichment
  versions record their parent evidence and selected stages. API credentials are never
  written there.
- Every run reports **text-sufficiency**: how many elements are usable from the
  markdown alone (prose, tables, verified LaTeX, vector-chart data) versus
  pixel-authoritative (a scan or photo where the crop is the real record). It's the
  honest measure of how close the output is to needing no `assets/`.
- `profile.json` and the generated README report independent quality dimensions for
  accounting, structure, text sufficiency, layout, OCR, equations, tables, figures,
  metadata, and unresolved errors. Ratios are evidence summaries, not probabilities.
  The old `confidence` field remains only for compatibility and is marked deprecated.
- Conservation evidence is layered. PDF-to-block word recall and whole-document numeric
  accounting remain broad diagnostics. A separate block-to-Markdown check records exact
  word and number conservation, expected formatting changes, and source-image dependence.
  Only unexplained block-to-Markdown loss or addition creates a new review action; each
  example names its page, bbox, block, and Markdown artifact in `profile.json`.

## Known limits (v1)

- **Equation enrichment is slow** (minutes for equation-heavy papers). `--no-formula`
  trades LaTeX for speed.
- **Suspect equations are image-backed.** When the engine's LaTeX disagrees with the
  page's text layer (or there's no text layer — a scan), the equation is cropped to a
  faithful image that becomes the authoritative source, with the text as a flagged
  hint. `--transcribe` upgrades that hint via local math-OCR. The recognizer receives
  blank context around the crop to avoid dropping symbols at its edges; the saved
  authoritative crop is unchanged.
- **Sub/superscripts** are recovered from glyph geometry on born-digital pages, on by
  default. A residual ceiling remains where the engine renders an exponent unlike the
  raw glyphs.
- **Book splitting depends on structural evidence.** Chapter bookmarks are preferred;
  numbered heading fallback is limited to Part containers to avoid turning references,
  index entries, or incidental “Chapter N” text into files. PDFs with neither signal
  remain split at their top-level bookmarks. Crops can still include journal furniture
  such as logos and banners.
- **`--describe` is an AI aid, not ground truth.** The description is labelled and the
  crop stays authoritative — verify specifics against the image.
- **Whole-page VLM transcription (`--ocr-page-vlm`) trades structure for a clean
  Markdown page.** It can recover prose and equations well, but table, equation, and
  caption elements are no longer separately addressable. A failed or looping read gets
  a visible marker and the page raster. Use the MinerU engine when element structure and
  equation-level crops matter.
- **Reader agreement is not ground truth.** OCR candidates can contain a plausible
  wrong digit that two readers share. The crop remains authoritative unless a cell
  matches a pinned external reference or a human verifies it.

## Methods and references

The table separates methods that affect conversion output from tools used only to
collect evidence. Links point to the primary paper when one exists, otherwise to
the official project or technical documentation.

| Method or system | How pdf2md uses it | Reference |
|---|---|---|
| Docling | Default layout, reading order, table structure, formula recognition, engine document model, and native parse/layout/OCR quality evidence | [Docling: An Efficient Open-Source Toolkit for AI-driven Document Conversion](https://arxiv.org/abs/2501.17887); [pipeline options](https://docling-project.github.io/docling/reference/pipeline_options/); [confidence scores](https://docling-project.github.io/docling/concepts/confidence_scores/) |
| MinerU | Optional full-document parser for scans and structurally difficult PDFs, isolated in its own environment | [MinerU: An Open-Source Solution for Precise Document Content Extraction](https://arxiv.org/abs/2409.18839); [official repository](https://github.com/opendatalab/MinerU) |
| PDFium / pypdfium2 | Source rendering, glyph geometry, page objects, outlines, crops, and source-independent verification | [PDFium documentation](https://pdfium.googlesource.com/pdfium/+/HEAD/docs/getting-started.md); [pypdfium2 documentation](https://pypdfium2-team.github.io/pypdfium2/) |
| RapidOCR with PP-OCRv4 models | Default offline OCR for scanned pages and printed figure labels | [RapidOCR](https://github.com/RapidAI/RapidOCR); [PP-OCRv4 technical description](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version2.x/ppocr/blog/PP-OCRv4_introduction.md) |
| wordninja | English-only re-segmentation when OCR joins words inside a line | [wordninja](https://github.com/keredson/wordninja) |
| Tesseract | Independent, non-authoritative table-cell and row-location evidence | R. Smith, [An Overview of the Tesseract OCR Engine](https://doi.org/10.1109/ICDAR.2007.4376991), ICDAR 2007 |
| Surya | Optional local re-transcription of image-backed equations | [Surya](https://github.com/datalab-to/surya) |
| GROBID | Optional scholarly header and reference parsing; merges only missing fields and preserves raw TEI | [GROBID documentation](https://grobid.readthedocs.io/en/latest/Introduction/); the project asks users to [cite the software rather than a paper](https://grobid.readthedocs.io/en/latest/References/) |
| PaddleOCR-VL / PP-OCRv6 | Pinned evaluation readers and non-mutating evidence overlays; not a production value resolver | [PaddleOCR-VL technical report](https://arxiv.org/abs/2510.14528); [PaddleOCR 3.0 technical report](https://arxiv.org/abs/2507.05595) |
| Matplotlib | Deterministic chart reproduction and optional equation render-back checks | J. D. Hunter, [Matplotlib: A 2D Graphics Environment](https://doi.org/10.1109/MCSE.2007.55), 2007 |
| Poppler `pdftocairo` | Optional SVG export for born-digital figures | [Poppler](https://poppler.freedesktop.org/) |
| OpenAI-compatible vision APIs | User-selected OCR, figure description, printed-label, and raster-chart models | Model-specific; pdf2md records the configured model and endpoint inputs in provenance without recording credentials. |
| JSON Schema Draft 2020-12 | Defines and validates the engine-neutral `passages.jsonl` record contract | [JSON Schema Draft 2020-12 specification](https://json-schema.org/draft/2020-12) |
| Docling chunking patterns | Reference behavior for tokenizer-aligned contextual text, line-aware splitting, and repeated table headers; pdf2md applies these rules to its engine-neutral passage records | [Docling chunking concepts](https://docling-project.github.io/docling/concepts/chunking/) |
| Hugging Face Transformers tokenizers | Optional model-aligned passage counting from a Hub model name or local tokenizer directory | [Transformers tokenizer documentation](https://huggingface.co/docs/transformers/fast_tokenizers) |

Project-specific methods, including glyph-cell verification, layered numeric and word
conservation, vector-path chart recovery, refusal-first OCR evidence, structure-aware
chunking, deterministic document maps, explicit local symbol indexing, and the
source-grounded agent benchmark, are documented in the focused records listed under
[Development and evaluation](#development-and-evaluation). They were developed and
measured in this repository rather than taken from a single paper.
