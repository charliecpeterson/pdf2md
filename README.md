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
option for scans and difficult tables or equations, and
[Marker](https://github.com/datalab-to/marker) is the best-reading option where a
GPU inference server is available. pdf2md supplies the common document model,
logical-section splitting, bibliographic front matter, source crops, born-digital
chart digitization, and per-document coverage audit.

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
| Scans and difficult tables/equations | MinerU native structure through a separate CLI environment | Production option selected by the labelled bake-off; not run as a blanket second parser. Measured over ten scanned documents converted both ways on one machine at one revision: 217 tables against Docling's 138, a structural finding on 51% of them against 92%, 12% more clean values at a lower malformed rate (5.3% against 7.7%), in 19 minutes against 31. It never found fewer tables on any document. Counts of a given finding are not comparable between engines without asking how many tables the check could judge: `decimal_separator_lost` reads 6 against 12 until you notice Docling offers the check a judgeable column in 13 of 138 tables against MinerU's 155 of 217, at which point it is 46% against 8%. |
| Best measured reading quality | Marker's JSON block tree through a separate CLI environment | Opt-in. Reads better than Docling on every olmOCR-bench subset (71.5% against 55.4% through pdf2md) and is the only engine here that emits inline mathematics as LaTeX. Needs a GPU inference server, and its output is not reproducible run to run, so the `qa.py --check` invariants cannot be enforced against it. |
| Page rendering and exact PDF evidence | PDFium through pypdfium2 for glyphs, page rasters, crops, outlines, and vector objects | Production evidence layer, independent of the parser adapter. |
| Clean scanned prose | RapidOCR followed by conservative punctuation repair and English word re-splitting | Default offline fallback. Word splitting is disabled for non-English scans. |
| Whole-page OCR | OCR-focused VLM through an OpenAI-compatible endpoint | Opt-in. It can improve page text but collapses table, equation, and caption structure into one Markdown block. |
| Equations | Docling LaTeX checked against the embedded text layer; suspect results become image-backed | Production. Surya re-transcription and Matplotlib render-back comparison are opt-in evidence. `Equation text coverage` counts only equations whose text stands without the crop, so a scan reads `none (0/11)` with all 11 LaTeX strings present: every formula-enabled document in the corpus transcribes 100% of its equations, and what varies is how many the page's own layer could confirm. A page with no layer cannot judge its equations either, so those findings are informational. |
| Scans carrying an OCR text layer | Detected from a full-page image plus invisible (render-mode-3) text, and treated as a scan | Production. This is the one case where a text layer exists but is not the page's own words, so every glyph check would otherwise confirm the engine's errors instead of catching them. |
| Reading order | Page columns recovered from block geometry, plus the document's own numbering where a page carries an unbroken run of ordinals | Production. The rest of the audit is order-insensitive by design, so an interleaved two-column page conserves every word and number and still reads as nonsense. The numbering path is proof rather than inference but only covers bibliographies the engine emits one entry per block; the geometric path covers the rest. |
| Prose against the text layer | Per-block word recall on a script-split, hyphen-joined reading, with accent damage separated from missing words | Production. Missing words are an action beside the block; lost diacritics are recorded without burying a bibliography in markers. |
| Symbols in prose | Greek letters and math operators present in the block's glyph region and absent from its emitted text; a dropped comparison (`≥ ≤ ≈ ∼ …`) is raised at high severity | Production, reported never repaired. Word recall cannot see this: a 200-word paragraph that loses one `χ` scores 0.995 and passes, so 40 blocks across a 28-paper corpus drop a symbol and 4 of them are low-recall as well. Measured at 0.97 precision against poppler, with 0.3% of loss going unraised. A lost comparison is separated because it is the only case that reads as ordinary text afterwards (`to be ≥1.6` → `to be 1.6`); 4 of 71 corpus findings carry one. |
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
work is recorded in the [quality and ingestion plan](docs/archive/quality-and-ingestion-plan.md).

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

Marker likewise, and it additionally needs an inference server. Its Surya backend
tries to spawn a Docker container with the `nvidia` runtime, which many hosts do
not register even with the container toolkit installed; starting the server by
hand avoids that entirely:

```bash
uv venv env/marker --python 3.12
uv pip install --python env/marker/bin/python marker-pdf

scripts/start_surya_vllm.sh                              # --gpus all, no nvidia runtime needed
export SURYA_INFERENCE_URL=http://127.0.0.1:8000/v1
```

`pdf2md doctor --engine marker` reports which half is missing.

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

`pdf2md doctor` names the accelerator the machine will actually use
(`accelerator device: mps (device = auto)`), which is the thing to check before copying a
corpus to one box rather than another; the resolved device is recorded in each bundle's
provenance, because Docling's layout detector makes marginally different calls on each.
There is no `--jobs` — run several processes to parallelise a batch, which is safe on one
output root — and `OMP_NUM_THREADS` caps each process's CPU appetite (default 4). See
[docs/options.md](docs/options.md#hardware-threads-and-running-more-than-one-convert).

Defaults are tuned for a born-digital journal paper and need no flags:
equation→LaTeX, inline sub/superscript recovery, and vector-chart digitization are
all **on**. Re-running the same file with the same effective inputs reuses its completed
version unless `--force` is set or optional model work needs a partial-run retry. Directory
conversion prints cached, converted, and failed totals when the batch completes.

## Where the rest is

| | |
|---|---|
| [docs/options.md](docs/options.md) | every flag, which PDF wants which, and worked recipes |
| [docs/output-format.md](docs/output-format.md) | what a bundle contains — the `FORMAT_VERSION` contract |
| [docs/methods.md](docs/methods.md) | how each behaviour is measured, and against what |
| [docs/known-limits.md](docs/known-limits.md) | what this does not do well |
| [docs/qa-corpus.md](docs/qa-corpus.md) | the labelled corpus and where to keep it |
| [docs/](docs/) | everything else, including the archived workstreams |
| [scripts/README.md](scripts/README.md) | the 83 evaluation harnesses |
| [notes/](notes/) | field reports from people who used it for real work |
| [CHANGELOG.md](CHANGELOG.md) | what changed, by release |
