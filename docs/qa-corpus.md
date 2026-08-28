# QA corpus

QA results are valid only for the exact source bytes named in the label files.
The checks reject a source when its recorded SHA-256 does not match.

## Restored sources

| File | Origin | Pages | SHA-256 |
|---|---|---:|---|
| `1706.03762v7.pdf` | [arXiv v7](https://arxiv.org/pdf/1706.03762v7) | 15 | `bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697` |
| `2207.10841v3.pdf` | [arXiv v3](https://arxiv.org/pdf/2207.10841v3) | 37 | `416d651377c4cce7ec6c91680afba2764f1d4e549525bc657d147faf6c5e0ef0` |
| `slater-50page.pdf` | pages 1 through 50 of the pinned Slater volume | 50 | `1694f97480e14a318ca88fa3350634d797cb9248b9bc367582a6d03ca9a2c5bd` |
| `GRASP2018-manual.pdf` | GRASP2018 manual | 327 | `bf0e2756389dfaa7fcec6bb67b5217d8aa4e9902b969e44234e5a7255c037687` |

The Slater slice was rebuilt from
`slater-quantum_theory_of_atomic_structure-vol1.pdf`, whose SHA-256 is
`cc30d2877cfb32b45135a6ab36dfb903eddcacaf8427f025d482074bb3ca56d6`.
Poppler can reproduce the content slice:

```console
mkdir -p ~/scratch/pdf2md-slater50
pdfseparate -f 1 -l 50 \
  slater-quantum_theory_of_atomic_structure-vol1.pdf \
  ~/scratch/pdf2md-slater50/page-%03d.pdf
pdfunite ~/scratch/pdf2md-slater50/page-*.pdf slater-50page.pdf
```

Poppler's PDF serialization can vary across versions. The committed derived PDF
is the pinned QA source; the recipe documents its page provenance rather than
promising byte-identical output from every Poppler release.

## Retired historical entries

The following historical sources were not found in the project tree, repository
history, the wider projects directory, or Spotlight's local index:

- `094103_1_online.pdf`
- `atkins-50page.pdf`
- `papertest.pdf`
- `physics-50page.pdf`

They have been removed from the active baselines. Guessing would defeat the hash
gate: `094103_1_online.pdf`, for example, is a publisher-generated filename shared
by unrelated papers. The two pinned arXiv papers cover born-digital and multi-column
layouts, the Slater slice covers scanned equations, and GRASP covers book structure
and broken fonts. The native bake-off adds exact table, equation, raster-figure,
and chart facts. Git history retains the retired numeric baselines if the original
bytes are recovered later.

## Verification

Evaluator exit flags have one user-facing meaning each:

- `--check` compares a result with a hash-pinned corpus or regression baseline.
- `--strict` enforces that evaluator's declared release criteria and treats
  execution errors as failures. Refusal policy is evaluator-specific: extraction
  evaluators fail `tool_refused`, while the agent benchmark records cautious model
  refusals without treating them as incorrect scientific claims.

Older scoring scripts retain a hidden `--check` alias for `--strict` so existing
commands do not break, but new commands and documentation use the explicit name.

Keep current conversions for all four baseline documents directly under one
output root. Nested benchmark or smoke-test directories are not a QA corpus.
Run:

```console
uv run python scripts/qa.py out/qa-current --check
uv run python scripts/eval_equations.py out/qa-current --check --strict \
  --report out/reviews/equation-components-v1.json
uv run python scripts/eval_equation_recovery.py --check \
  --report out/reviews/equation-recovery-v1.json
uv run python scripts/eval_accuracy.py out/qa-current --strict
uv run python scripts/eval_book_splitting.py --check
uv run python scripts/eval_figure_accuracy.py --check \
  --report out/reviews/figure-accuracy-v1.json
uv run python scripts/eval_internal_scientific_checks.py --check \
  --report out/reviews/internal-scientific-checks-v1.json
uv run python scripts/eval_active_review_heldout.py --check \
  --report out/reviews/active-review-heldout-v1.json
uv run python scripts/eval_natural_rendering_stability.py compare \
  out/reviews/rendering-stability-natural-errors-v1/run.json --check
uv run python scripts/eval_blind_corpus.py SOURCE_DIR OUT_DIR --check --strict
```

The blind PDF corpus is a release-readiness check, separate from labelled accuracy.
Its ten arXiv URLs, hashes, and page counts were frozen before any conversion output
was inspected. The run uses `--no-formula` so it remains portable to a clean Linux
GPU environment without Python development headers; equations therefore stay
source-cropped and reviewable. The gate checks source identity, exact page counts,
block accounting, main-artifact presence, and review burden. It cannot establish
cell, equation, reading-order, or semantic accuracy.

The frozen Linux 4090 run covers 200 pages. All 10 sources match their hashes and
page counts; all 10 conversions have exact block accounting, structural completion,
and a main content artifact. Four documents require review, with 41 markers total.
Those markers remain a measured review burden, not failures hidden by the structural
summary.

The 2026-08-15 equation checkpoint locates all 12 labels across four documents.
Eight equations are semantically exact. Selected-output component accuracy is
53/55 symbols, 24/24 signs, 10/11 fractions, 17/18 delimiters, 26/30 subscripts,
and 24/27 superscripts. The corpus pins its labels, sources, conversion provenance,
evaluator, aggregate scores, and per-equation outcomes.

An equation label resolves to an equation block at its recorded ID. If conversion
changes the ID, the evaluator searches only equation blocks on the labelled source
page. Whole-page Slater OCR stores equations inside page transcription blocks, so
those four labels resolve through their pinned page transcription. This type and page
check prevents a nearby paragraph from satisfying a stale equation ID.

The equation-recovery gate pins six source crops to their provenance bboxes and
embedded-glyph readings, then scores preserved Surya 0.17.1 outputs. Eighty pixels
of blank context prevent crop-edge token loss. The measured production path raises
full exactness from 8/12 to 10/12 and leaves only the scanned Slater `rho_nu`
subscript unresolved. Three exact crop-backed controls do not regress; the other
five exact equations never enter the changed path. A 300 dpi Slater diagnostic is
fully exact but is not production-eligible because whole-page VLM transcription
does not retain a separate equation region.

The figure gate covers 27 scientific figures in five documents from AIP Publishing,
Academic Press, NASA, and arXiv. It independently pins scientific-content boxes,
caption associations, initial-block dispositions, excluded furniture regions, and
logical-fragment groups. The frozen result retains 27/27 figures at their exact
labelled content boxes, associates 14/14 labelled captions, excludes 9/9 furniture
regions, removes 5/5 labelled AIP promotional graphics, and reduces three fragmented
logical figures to zero. The corpus includes dense multipanel and inset plots,
cross-column captions, separately detected panel titles, a mixed text/equation
graphical abstract, continued figures, and low-quality scans.

The exact internal scientific gate evaluates 24 source-declared relations across the
Attention and NASA WATE-S documents. Twenty-one relations agree, two natural NASA OCR
errors receive review flags, and one alphanumeric numeric token is refused. The gate
uses exact decimal arithmetic without tolerances and cannot emit replacement values.
It covers repeated values, a printed component total, parameter symmetry, and
attention-width conservation. Rounded Fischer radial normalization stays in the
separate approximate consistency gate.

The held-out review gate removes each error-bearing document in turn, fits its seven
evidence-signal weights on the remaining 30 documents, then freezes the ranking before
scoring the excluded labels. At five reviews per document, active review finds 8/14
errors versus a 3.74 mean for confidence-stratified review. NASA and Slater contain
fixed-budget exceptions, so confidence-stratified review remains the production
default.

The rendering-instability gate adds 14 human-boxed natural primary errors to the
existing 56 clean controls and reads all 24 frozen rendering variants with the same
PP-OCRv6 model. Instability identifies 13/14 errors while also marking 15/56 controls:
0.929 sensitivity, 0.732 specificity, and 0.976 negative predictive value. The frame
is error-enriched, so its 0.464 positive predictive value is not a field prevalence
estimate. Instability remains review-ranking evidence and cannot verify or replace a
cell.

The new-layout column gate adds four source-pinned panels and seven deterministic
variants, for 245 row cases spanning weak separators, rule-free Fortran output,
proportional long labels, nine narrow numeric lanes, and combined blur/downsampling.
Repeated-row consensus initially made 37 wrong mappings on the long-label table. An
exact-count persistent-ruling check changes those failures to seven refusals and
raises the final result to 238 exact mappings with zero wrong. The earlier 798-case
column gate remains unchanged. Typed consensus reaches only 188 exact mappings and
refuses 57, so neither experimental locator is promoted to production.

The post-experiment promotion synthesis joins the frozen numeric-confidence,
held-out-review, rendering-stability, internal-relation, and new-layout reports. It
keeps automatic OCR value promotion undefined: only two proposed replacements have
labels, their zero-error 95 percent Wilson upper bound is 65.8 percent, and a learned
threshold admits one held-out wrong read. An audit of four current external-source
candidates finds no independent semantic fields that overlap extracted values.
Exact user-supplied references remain eligible for override; every other signal stays
support, review-ranking, or evaluation evidence. Run
`scripts/eval_promotion_decision.py --check` to verify the pinned decision.
