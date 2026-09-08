# Methods and evaluation

How each behaviour is measured, and against what. Every number in the
[README](../README.md) that came from a measurement was produced by a
harness named here; `scripts/README.md` is the tour of those harnesses.

## Development and evaluation

The commands below are for maintaining accuracy gates and reproducing experiments.
They are not needed for normal conversion.

Current design and user-facing behavior live in this README. The focused records
hold the details that would make the main document unreadable:

- [Engine bake-off](engine-bakeoff.md) and
  [bake-off results](bakeoff-results.md)
- [Dense numeric tables](dense-numeric-tables.md)
- [Scan-degradation benchmark](scan-degradation-benchmark.md)
- [Figure-to-text methods](figure-to-text.md)
- [Agent benchmark](agent-benchmark.md)
- [Completed quality, performance, and ingestion plan](quality-and-ingestion-plan.md)
- [Accuracy ideas, implemented experiments, and remaining candidates](accuracy-improvement-notes.md)
- [Archived original project plan and decision log](archive/PROJECT_PLAN.md)

<details>
<summary>Accuracy experiments and evaluator commands</summary>

The development [agent benchmark](agent-benchmark.md) compares answers from
page chunks or stable passages against answers from pinned source-PDF pages. It records
accuracy, citations, opened assets, review flags, and input tokens. A matched
11-question regression gives chunks and passages 11/11 correct answers and 11/11 valid
page citations; passages use 41.8 percent fewer input tokens. Its numeric extension
also scores structured evidence fields, refusal behavior, cross-block joins, derived
arithmetic, and source-crop provenance. The first Fischer run passes 5/6 tasks; the
remaining failure is agent arithmetic rather than extraction.

The [dense-table notes](dense-numeric-tables.md) describe the separate image,
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

The [controlled scan-degradation benchmark](scan-degradation-benchmark.md)
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
[dense numeric tables](dense-numeric-tables.md#scientific-support-across-independently-printed-data)
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
