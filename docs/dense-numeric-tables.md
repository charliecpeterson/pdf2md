# Dense scanned numeric tables

Dense scanned tables need two outputs: pixels for authority and cells for use. The
cells are valuable for search, comparison, and downstream analysis, but one plausible
wrong digit can invalidate a scientific result. pdf2md therefore keeps the two roles
separate.

For each structured table, the bundle writes the engine's untouched transcription to
`data/tables/<block>.md`, its raw cell grid to CSV, and a JSON sidecar with the source
crop and authority. On an OCR-backed page, Markdown shows the crop first and labels the
structured form as an OCR candidate. The candidate does not make the table complete or
text-authoritative.

Side-by-side tables often repeat one column schema for several atoms, methods, or
conditions. When pdf2md detects that shape, it splits panels even when their widths
differ and carries the schema into a following headerless block. Table I is parsed as
independent vertical lanes because the left and right records advance at different
rows. Atomic number, symbol, term, and configuration become separate fields. Scalar
properties such as `TOTAL ENERGY =` use `column=value`; they are not mislabeled as an
orbital statistic merely because OCR placed the number under that column. Inline
forms such as `TOTAL ENERGY = -11226.97` are split into a label and numeric value.
When an atomic number is present, the element symbol is constrained by the periodic
table while the OCR title remains available unchanged. The page-level JSON retains
source coordinates and raw rows. The matching long-form CSV uses these columns:

```text
panel,title,atomic_number,symbol,term,configuration,row_key,column,value,
raw_value,numeric_value,value_status,source_block_id,source_row,source_column,
primary_value,reader_value,best_value,confidence,resolution_basis,
validator_preference,validator_basis,verification_status,reader_refusal_reason
```

`raw_value` always preserves the engine output. `numeric_value` is set only when the
cell parses as a number. Spaces used for thousands or fractional digit grouping are
removed from `numeric_value`, while they remain in `raw_value`. Dot and dash placeholders get `dot_placeholder` or
`dash_placeholder`; their consumer-facing `value` is empty. Unnamed spacer columns do
not become long-form rows.

The page-level JSON also records two kinds of checks:

- Structural issues, such as mismatched row keys across adjacent panels or a
  non-increasing numeric row key. These fail the structural check.
- Structurally ambiguous panel rows. A repeated row key one cell before a persistent
  panel boundary, or a populated row ending in an unexplained blank while adjacent
  panels are complete, is omitted from consumer records. The raw cells, source row,
  panel, and refusal reason remain under `panels[].refused_rows` and
  `checks.issues`; the raw table CSV and crop remain unchanged.
- Numeric review signals. Five-point local continuity, fixed-decimal consistency, and
  numeric-type checks can queue a cell for inspection. None supplies a replacement.

Whole-document second-reader evidence is opt-in:

```bash
uv run pdf2md convert paper.pdf \
  --engine mineru \
  --mineru-executable env/mineru/bin/mineru \
  --table-ocr-executable tesseract
```

Each source table then gets `data/tables/<block>.cells.jsonl`. Numeric cells report
`agree`, `disagree`, or `reader_refused`; headers and placeholders report
`not_applicable`. Agreement produces `reader_agreement`, not `externally_verified`.
The aligner may skip extra OCR lines or leave individual source cells unmatched. It
refuses a whole grid only when fewer than half of the primary numeric cells can be
mapped or fewer than half of the mapped reader values parse as numbers.

Every numeric evidence record also has a consumer-facing `best_value`, `confidence`,
and `resolution_basis`. The same fields are joined into normalized CSV and JSON rows.
The confidence classes mean:

- `verified`: an external reference supplied the value.
- `high`: the primary and independent reader agree exactly after OCR character
  normalization.
- `low`: one reader was unavailable or the readers disagree. The primary value is
  retained and the basis says why.
- `not_applicable`: text, blank cells, and placeholders.

Resolution never changes `raw_value`, `reader_value`, or `verification_status`.
Continuity, decimal-format, and numeric-type rules may fill `validator_preference`
and `validator_basis`, but they cannot rewrite `best_value`.

An external reference CSV uses semantic keys:

```text
atomic_number,row_key,column,value
29,0.100,1S,0.3900
```

Pass it during conversion with `--table-reference reference.csv`. Its path and SHA-256
join the run fingerprint. Cells become `externally_verified` only on an exact semantic
match; missing keys report `no_reference`. Run the separate gate with:

```bash
uv run python scripts/compare_table_reference.py out/<doc>/v<n> \
  --reference reference.csv \
  --report out/table-reference-report.json \
  --strict
```

The gate reports `agree`, `disagree`, and `tool_refused`. Without a reference it emits
`no_reference` and fails under `--strict`; it never treats a later ATSP calculation as
the 1972 table unless the inputs and conventions have been pinned separately.

## Scientific support across independently printed data

Two Fischer-specific adapters test scientific relationships without promoting a
replacement digit:

```bash
uv run python scripts/eval_atsp_fischer_reference.py --check
uv run python scripts/eval_fischer_radial_consistency.py --check
```

The ATSP adapter authors atomic number, configuration, term, and orbital keys outside
the OCR output, runs term-specific Hartree-Fock calculations, and compares 153 mapped
Table I fields. The frozen run reports 152 scientifically consistent values, no
disagreement, and one missing extracted value. Only 40 values are exactly equal. The
later code shares Fischer method lineage, so the outcome is `scientific_support`,
never `externally_verified`.

The radial adapter joins separately printed Tables I and II. It integrates the Table
II radial functions to recompute orbital normalization, `<1/r>`, `<r>`, and `<r^2>`,
then compares the moments with Table I. On v6, 17 of 23 orbitals are scientifically
consistent and six are `tool_refused`; none disagree. The six refusals belong to the
fluorine and neon panels whose tail rows lost placeholder cells and shifted column
boundaries. Accepting the truncated grids would turn missing structural evidence into
false scientific disagreements.

Version 6 is a controlled postprocessing replay of the exact v5 extraction. Raw table
CSV and cell-evidence hashes are unchanged; only normalized-table artifacts and value
resolution are rebuilt. `scripts/replay_table_postprocessing.py` records the source
version, source provenance hash, current implementation hash, and replay scope in the
new version's provenance.

A fresh MinerU 3.4.4 GPU parse was also preserved, but not promoted. It emitted 144
tables instead of 145 and truncated several radial grids. Its runtime reported a
changed Transformers fast-image-processor default, so the result shows that a
top-level MinerU version string does not pin the transitive environment. The native
JSON, Docker image ID, command, and comparison live in
`out/reviews/fischer-v6-native/manifest.json`.

Three follow-up parses hold the source hash, immutable container image, embedded model
snapshot, `use_fast=True` preprocessing path, zero-temperature sampling, seed,
Python hash seed, cuBLAS workspace setting, and offline execution fixed. All three
still produce different middle-JSON hashes. Block geometry is stable: every run has
305 blocks, 144 tables, 12 figures, and 1.0 pairwise block recall at IoU 0.9. Content
is not stable. Pairwise table-content identity ranges from 111/144 to 119/144, and one
run shortens the page-18 radial grid from 99 to 91 GFM rows. The image and dependency
pin therefore removes environment drift but does not make GPU inference repeatable.
The v5 extraction remains authoritative. Reproduce and check the frozen comparison
with:

```bash
scripts/run_pinned_mineru_repeats.sh SOURCE_PDF OUTPUT_ROOT 3
.venv/bin/python scripts/eval_mineru_repeat_stability.py --check
```

Exact-cell evaluation is source-hash-pinned:

```bash
UV_CACHE_DIR=/private/tmp/pdf2md-uv-cache \
  uv run python scripts/eval_numeric_tables.py out \
  --labels tests/numeric_table_labels.json \
  --tesseract \
  --report out/numeric-table-report.json \
  --strict
```

Each labelled cell reports `agree`, `disagree`, or `tool_refused`. `--strict` returns a
nonzero status for any disagreement, refusal, or empty evaluation. The current
55-cell diagnostic set intentionally contains five known primary errors, so that
strict command currently exits 1 after reporting them. Use
`scripts/eval_natural_numeric_error_corpus.py --check` for the passing hash-pinned
expected-outcome gate. Add labels only after reading the source pixels, and pin the
source SHA-256 so a different scan cannot silently reuse them.

A separate manifest runs source-checked labels across output roots and keeps known
errors distinct from controls:

```bash
uv run python scripts/eval_scanned_numeric_corpus.py \
  --report out/reviews/scanned-numeric-corpus-report.json \
  --check
```

The current corpus has 124 cells from six scanned documents. Twenty are known primary
errors: six Fischer atomic-structure cells, the Slater cobalt 3s value, eight ORNL
Fortran-output values, and five NASA dot-matrix values. The other 104 cells are
source-checked controls, including 12 values from a dense two-lane NIST spectroscopy
table. The baseline is 104 agreements, 20 known disagreements, and zero primary
refusals. `--check` tests those exact per-case counts instead of requiring intentional
error cases to pass.

The added natural errors cover sign loss, Fortran `D` marker substitution,
digit-to-letter confusion, and plausible digit substitution. The existing resolver
corrects none of the 14 non-Fischer errors. Auxiliary Tesseract produces two raw
disagreements on those error cells and refuses 12; neither disagreement supplies a
source-correct numeric replacement. This is the first correction-recall estimate for
natural non-Fischer errors, but the error-enriched sample is not a promotion bound.

To score the reader evidence already written by conversion, replace `--tesseract`
with `--cell-evidence-reader`. This evaluates the exact values consumers see in
`*.cells.jsonl` and hashes the evidence files into the report. It does not rerun a
more permissive OCR path.

For a bounded third-reader experiment, `scripts/prepare_paddleocr_crops.py` accepts
`--reader-disagreements REPORT` and `--max-cells N`. The limit samples round-robin
across source tables so one large grid cannot consume the entire GPU run. The crop
manifest embeds the source-pinned labels used to select the cells.

Generate a source-linked review sheet without rerunning conversion:

```bash
uv run pdf2md review-tables out/<doc>/v<n> \
  --sample 120 \
  --labels tests/numeric_table_labels.json \
  --output out/reviews/paper.html
```

Sampling is deterministic for a given seed, stratified by confidence, and spread
across source tables before filling the remaining quota. Existing labels are always
included and prefilled. The downloaded JSON uses the evaluator's label schema.

`--tesseract` adds an independent Tesseract 5 read of only the labelled table
crops. The adapter uses TSV word coordinates to align each reading with the
candidate grid. It refuses the table when row or column alignment is ambiguous.
The report measures Tesseract's exact-cell accuracy and whether disagreement
between the two readers detects MinerU errors.

The labelled corpus spans scanned dense grids and two born-digital papers with grouped
digits, sparse cells, and multi-level headers. It covers both primary-reader errors and
common second-reader artifacts, including invented leading digits. Reports score the primary extraction,
the independent reader, disagreement as an error detector, and the resolved
`best_value` separately. A resolver change is acceptable only when it improves exact
label agreement without hiding refusals or unresolved disagreements.

The fixed-font glyph diagnostic has a separate 77-cell calibration set in
`tests/glyph_table_labels.json`. Its OCRFlux inputs are hash-pinned, and each document
pins the conversion version used for cell coordinates. Reader agreement supplies the
atlas values; source-read labels only score the result. Horizontal table rules are
discarded before character segmentation. Leave-one-cell-out validation is exact on
48 chemistry cells and 28 Transformer cells, with one Transformer crop refused. These
clean agreements do not calibrate an automatic correction threshold because they do
not include enough real reader disagreements or known glyph-reader failures.

Two source-checked disagreement sets cover 38 more cells. Slater contributes three
same-length digit substitutions that the glyph gate ranks correctly and one malformed
candidate it refuses. `pdf-parse-bench` document 059 contributes four malformed or
length-changing candidates, all refused. Across their agreement cells, leave-one-out
validation is exact on 27 comparable cells and refuses three cells whose atlas lacks
a character. These results test the refusal boundary and add another scanned font,
but do not justify production correction on their own.

The Fischer labels provide that missing direction. Tesseract reads four wrong primary
cells correctly; the glyph gate accepts three corrections and rejects the correct
reader value once, keeping `-0.1795` instead of source-checked `-0.7795`. On the same
crops, an atlas learned from Paddle agreement chooses `-0.7795`. The Tesseract choice
is stable across all 21 leave-one-atlas-cell-out trials, while the correct Paddle
choice changes in one of 23 trials. The report retains these jackknife counts so the
failure stays reproducible, but they must not be presented as calibrated confidence.
The glyph diagnostic remains useful for review ordering and candidate comparison,
not automatic correction.

A selective third-reader cascade performs better on the current labels. It invokes
PaddleOCR-VL only when the primary and Tesseract both return numeric values and
disagree. Paddle must match exactly one existing candidate; a third value or refusal
leaves the cell unresolved, and an existing continuity or fixed-format decision may
veto the majority. Across 93 pinned cells in five documents, this reduces 93 possible
Paddle calls to 29. Paddle returns a usable value for 24 and refuses five. Four of five
primary errors are corrected, no correct primary is changed, and the remaining error
is a three-way split (`0.0709`, `9.9709`, `0.0209`). The aggregate result is 92/93
correct versus 88/93 for the primary extraction.

This result does not authorize production replacement. All five primary errors in
the original cascade trial come from Fischer, the validators never veto a majority in
that measured set, and 24 usable adjudications do not bound the risk of correlated OCR
errors.

The non-Fischer follow-up runs pinned PP-OCRv6 on all 14 Slater, ORNL, and NASA
errors. Its source-pixel boxes were human-verified and hash-pinned before recognition,
so geometry does not come from MinerU or Tesseract tokens. All 14 boxes prepare with
zero geometry refusals. Raw numeric reads contain three correct values, three wrong
values, and eight refusals. At the frozen 0.99 threshold, two ORNL `D`-exponent values
are accepted correctly, no wrong values are accepted, and 12 cells are refused. The
Slater `8.0` read is correct at 0.947 and remains refused.

The preserved disagreement-only cascade triggers on two cells and corrects neither
because both third reads are below threshold. The two accepted ORNL values occur on
cells where Tesseract refused, leaving only one acceptable auxiliary reading. They
remain review evidence rather than promoted replacements. The per-document result is
0/1 accepted for Slater, 2/8 for ORNL, and 0/5 for NASA. Reproduce the hash-pinned
result with `scripts/eval_natural_error_third_reader.py --check`.

A broader `pdf-parse-bench` check now source-pins 1,355 scalar cells across 15
born-digital documents. Its conservative mapper accepts only equal-length scalar rows
where all but at most one value already match the independent LaTeX ground truth;
structural omissions remain refusals. Docling's primary and retained `best_value` are
1,355/1,355 exact on this slice.

The reference slice has since expanded to 1,812 exact accepted cells across 25
documents, including ten deliberately selected for complex tables. Those ten add 457
exact primary values, while conservative row-mapping refusals across the slice rise to
238. This is evidence that the hard born-digital cases currently fail through
structure and alignment before they produce wrong accepted digits.

The natural-error corpus combines that reference slice with complete extracted-table
reviews for Attention, the orbitals paper, and Dolg, plus targeted GRASP, Slater,
ORNL, NASA, and NIST source-pixel labels. Across 33 non-Fischer documents, 31 supply
at least one accepted source-checked cell. Primary values are exact on 2,099/2,113;
the 14 errors comprise Slater `8.9` for source `8.0`, eight degraded ORNL output
values, and five NASA dot-matrix values. Auxiliary Tesseract evidence has 1,078
agreements, 182 disagreements, and 853 refusals. These reader outcomes measure triage
coverage, not primary accuracy.

Structural outcomes stay outside the value denominator. The orbitals table merges six
scientific-notation gamma values into row-key strings instead of producing their data
column. Two Dolg tables with complex headers remain source-linked HTML rather than a
guessed rectangle. The 25-document LaTeX mapper refuses 238 rows that do not meet its
near-exact alignment rule. In the NIST table, the auxiliary reader emits 62 apparent
disagreements and 369 refusals across 431 cells; source review shows the apparent
disagreements are lane or row shifts, so all 431 are recorded as auxiliary-geometry
refusals. `scripts/eval_natural_numeric_error_corpus.py --check` hash-pins every
report, label set, source PDF, and manually reviewed crop, and emits rates by document,
sampling role, table family, typography, and reader. Because the inputs mix
representative-looking tables with targeted and error-enriched samples, the pooled
result is explicitly not a prevalence estimate.

The first production-evidence run had 246 Tesseract agreements, 154 disagreements,
and 955 refusals. Fixing row order, per-table column layout, short integer handling,
and exact numeric equivalence changed those counts to 759 agreements, 19
disagreements, and 577 refusals without changing a primary value. A further fix for
repeated secondary row labels yields 766 agreements, 16 disagreements, and 573
refusals. All high-confidence cells are correct; the 16 residual disagreements are
retained at low confidence and do not change the primary.

The validator falsification gate contains 18 clean synthetic cases with one explicitly
correct candidate each. Continuity makes three correct and six wrong preferences;
decimal-format consistency makes two correct and two wrong preferences. The combined
resolver makes seven correct preferences, eight wrong preferences, and three refusals.
Scored as automatic rewrites, that is four corrections and eight regressions. Run
`scripts/eval_numeric_validators.py --check` to verify the hash-pinned cases and
report. The rules remain useful for review ordering, but they are not evidence strong
enough to select a replacement value.

The third-reader trials found why two-of-three voting is still unsafe. Tesseract and
PaddleOCR-VL initially agreed on five wrong candidates because both received the same
wrong row crop. One table had rotated extracted rows; another repeated `Hybrid GS`
under several metric groups, and matching only the first text column selected an
earlier group. The OCR engines read those crops correctly, but the shared locator made
their votes correlated. Both geometry defects are fixed and covered by regressions.
An auxiliary reader counts as independent only when its region localization is also
independent or separately proved.

The source-row recovery trial now supplies that separate proof for its two inferred
Fischer panels. A token-free horizontal ink projection finds all 97 rows in each
panel, and all 194 Tesseract row centers fall inside the corresponding bands. The
cross-document alignment corpus repeats the check on 106/106 rows from five held-out
panels. This proves row identity for that recovery path; it does not make the OCR
values themselves independent or authorize automatic replacement.

A separate 28-case pixel stress corpus defines where that proof stops. Equal-width
panel splits produce 16 exact mappings. Detecting dominant vertical-whitespace gutters
from source pixels raises that to 23, including all five 30/70 through 70/30 layouts
and all six skew cases. Clipped data rows, denser noise, and a false footer can still
select shifted sequences, while a 32-pixel synthetic bow is refused. The production
cross-check refuses all five unsafe or ambiguous cases and accepts zero wrong
mappings. Re-running the Fischer recovery overlay keeps all 1,089 cell geometries and
hashes unchanged, including the 41/41 source-labelled result. Across the larger radial
corpus, detected bounds agree with all 7,886 pinned key-cell centers in 130/130
reference-bearing tables. The one table without prepared reference cells remains an
explicit skip.

Horizontal cell identity now has a separate source-pixel check. Within each accepted
row, character-sized gaps are merged into the structural number of column runs. All
1,089 prepared Fischer recovery cells have an independent run center inside their
pre-existing cell box, with no disagreements or refusals. The stress corpus verifies
1,602/1,792 available rows and refuses the other 190. A held-out typography check
finds 79/106 exact rows; the GRASP Mg III panel correctly refuses because its long
configuration strings create more runs than the structural column count. These are
geometry results only and do not validate the recognized digits.

Recognition is now measured separately on all 106 source-checked keys from those five
held-out panels. With the reader and 0.99 threshold fixed, Tesseract word-box crops
produce 103 agreements, one disagreement, and two refusals. Token-free projection
crops inside the known key lane produce 106 agreements with no refusals. A
refusal-only semantic fallback uses projection for the three failed original crops
and reaches 106/106. Two failures were adjacent-column contamination in GRASP; the
third was a correct Slater value with a sub-threshold score. This proves the key lane
path across three documents, not the less constrained data cells.

A 56-cell visually source-checked data sample now measures those less constrained
cells. All primary values are correct. Tesseract is semantically exact on 51/56; five
superscript `a` footnotes are misread as trailing digits. PP-OCRv6 accepts 52/56
existing crops and 51/56 independently localized projection crops, with no accepted
wrong value on either path. A refusal-only crop fallback accepts 53/56, using
projection once for the negative `-0.02146` and retaining refusals on footnoted
`0.10865` and two vertical-ellipsis placeholders. All eight scientific-notation cells
are exact on every path. The sample tests clean-cell confirmation, false alerts, and
the covered syntax, not correction recall.

The Fischer trial uses independent row bands and column runs as the crop geometry
for the same pinned recognizer. The tighter projection crop is complementary rather
than uniformly better: it gains 88 above-threshold reads and loses 67 compared with
the OCR-box crop. A refusal-only fallback keeps the original read whenever it passes,
preserves 41/41 reviewed labels, and raises accepted shifted-row recovery cells from
441 to 541. The remaining row-key and Tesseract-agreement gates still apply. Two
apparent fallback control errors were reviewed against the pixels and proved to be
real column shifts in the structured output; they now serve as divergence fixtures.

That refusal-only policy is now part of the source-row overlay producer. Preparation
writes `inputs.json` for the original crop and `inputs-projection.json` for the
independent crop. Application takes both pinned reader runs, tries the original
semantic gate first, and records `accepted_reader: projection` only after that gate
refuses. The v3 run preserves all 610 previous candidates and adds 137, for 747 total.
No converted table is rewritten; these remain traceable evidence-overlay values.

This benchmark is strong evidence against false automatic changes, but it contains no
primary extraction errors in the 1,355 safely mapped cells. The scanned corpus now
provides 14 natural non-Fischer errors across three documents, and the current
correction path fixes 0/14. The independent-geometry third-reader experiment reads
three correctly at raw score and accepts two correctly at the frozen 0.99 threshold,
but those two occur where Tesseract refused. Production replacement remains disabled
until held-out documents bound its false-correction rate.

Cell consensus compares aligned grids from independent readers. It records every raw
reading, exposes a unanimous or strict-majority candidate, and leaves a tie unresolved.
That output is evidence for review. Promotion to structured authority needs a measured
gate across multiple documents, not agreement on one table.

## Selective confidence calibration

`scripts/eval_numeric_confidence.py` combines the source-pinned evidence without
pretending that every signal has the same denominator. Its natural-cell view contains
2,113 labelled primary values from 31 documents with reviewed cells. Its PP-OCRv6
score view contains 56 held-out clean controls and 14 natural primary errors from five
unique source documents. The latter is deliberately error-enriched and cannot estimate
deployment prevalence.

```bash
uv run python scripts/eval_numeric_confidence.py \
  --report out/reviews/numeric-confidence-v1/report.json \
  --check
```

Reader agreement verifies 1,069 of 2,113 natural-corpus primary values with no observed
error. That is 50.6 percent coverage, with a 95 percent Wilson upper bound of 0.358
percent on the error rate. Reported `high` confidence covers 984 cells with no observed
error, but every one comes from the independently mapped born-digital reference slice.
The label distribution therefore does not show that the category transfers to scanned
or unfamiliar table families. Geometry availability alone accepts 1,991 cells and
contains 11 of the 14 primary errors.

| PP-OCRv6 score threshold | Accepted reads | Wrong reads | Proposed replacements | Corrections | Regressions |
|---:|---:|---:|---:|---:|---:|
| 0.00 | 60 | 3 | 5 | 3 | 0 |
| 0.90 | 59 | 2 | 4 | 3 | 0 |
| 0.95 | 56 | 0 | 2 | 2 | 0 |
| 0.99 | 54 | 0 | 2 | 2 | 0 |
| 0.995 | 50 | 0 | 0 | 0 | 0 |

The 0.99 point looks clean but does not support automatic replacement. Only two values
would change, so zero observed wrong replacements still has a 65.8 percent Wilson
upper bound. A threshold chosen for maximum training coverage with zero training
errors also accepts one wrong NASA read when NASA is held out. That read agrees with
the already wrong primary value, so it would create a false verification rather than
a replacement regression.

Crop-path agreement is available for all 56 clean controls and for none of the 14
natural errors, so it cannot estimate correction recall. Numeric-validator and
document-consistency support reaches one labelled cell. No labelled cell has an
external scientific reference yet. These are recorded as insufficient or unavailable,
not converted to zero-error claims.

Glyph margin also fails as a monotonic confidence score. Eleven of 17 reviewed
rankings have comparable margins; two are wrong, including one at the third-largest
margin. All eight Fischer rankings with jackknife stability remain stable, but two are
source-confirmed wrong. The signal is still useful for review ordering.

The report, all inputs, full score curves, and leave-one-document-out folds are pinned
in `tests/numeric_confidence_corpus.json`. The measured conclusion is
`promotion_gate: not_defined`.

The decision was rerun after the later held-out review, natural rendering-stability,
exact internal-relation, and new-layout geometry experiments:

```bash
uv run python scripts/eval_promotion_decision.py --check
```

The synthesis still records `automatic_ocr_value_promotion: not_defined`. At the
0.99 reader threshold, only two proposed replacements have labels, so zero observed
wrong replacements retains a 65.8 percent Wilson upper bound. A learned threshold has
one held-out wrong read. Rendering instability catches 13/14 natural errors but also
marks 15/56 clean controls. The 24 exact internal checks and the geometry gates can
support or flag cells but cannot supply replacement values.

`tests/external_reference_audit.json` records the external-source search. Basis Set
Exchange currently has no BFD family. PySCF supplies BFD basis and pseudopotential
parameters, but those fields do not occur in the extracted BFD result tables. The
historical author link is unavailable. The arXiv 2207.10841 source bundle contains
manuscript source and figure PDFs but no independent data attachment. The audit
therefore adds no nominal adapter. The existing semantic adapter remains eligible
when a user supplies an exact independent reference with matching fields.

The source list, input hashes, decision, and bounded evidence summary are frozen in
`tests/promotion_decision_sources.json`, `tests/promotion_decision_corpus.json`, and
`out/reviews/promotion-decision-v1.json`.

## Active review sampling

`scripts/eval_active_review_sampling.py` asks which cells a reviewer should see first.
Its active rank uses only evidence available before source review: confidence, reader
disagreement and refusal, geometry refusal, malformed numeric syntax, validator or
resolver conflict, and novelty across documents, table families, and typography.
Review labels and corpus roles are excluded from ranking. Crop-path and preprocessing
instability have no shared natural-cell coverage in this frame and remain explicitly
unavailable.

```bash
uv run python scripts/eval_active_review_sampling.py --check
```

The frozen frame contains 2,113 cells from 31 documents, including 14 known primary
errors. At a budget of 40 cells, active review finds 11 errors. Uniform random review
finds 0.26 on average over 1,000 seeded trials, and the current confidence-stratified
policy finds 5.286. The advantage narrows with budget: at 120 cells active finds 12,
while confidence-stratified review averages 11.987; at 200 cells the counts are 13 and
13.228.

This is an error-enriched, coverage-oriented frame. It estimates error-discovery yield
within the frame, not deployed error prevalence. The active weights were also tested
on the corpus that motivated their signals, so production keeps the simpler
confidence-stratified default until active sampling wins on held-out documents. The
full ordering, baseline distributions, unavailable signals, and no-prevalence contract
are pinned in `tests/active_review_sampling_corpus.json`.

## Column geometry method comparison

The row-local projection method requires each row to produce the structural number of
ink runs. That gives 87 exact rows and 27 refusals across six source-checked layouts.
All 26 refusals in GRASP Mg III come from a legitimate long configuration field: its
internal gaps create 9 or 10 runs in an 8-column table. Increasing the global merge
distance would erase valid narrow columns elsewhere.

`scripts/eval_column_geometry_methods.py` compares that baseline with fixed lanes
read from the header, separators supported across repeated rows, and the same
consensus constrained by numeric-versus-text lane types. Recognized cell values are
never supplied to these paths.

```bash
uv run python scripts/eval_column_geometry_methods.py --check
```

Repeated-row separator consensus recovers all 26 Mg III rows and yields 113/114 exact
rows overall. The one refusal is the existing partial final Fischer row, which lacks
one structural cell. Lane typing produces the same 113/114 result; it validates that
multiple fragments stay inside declared text lanes but does not recover an additional
row. All 56 held-out labelled-cell ink centers map to the correct consensus lane.

Five born-digital panels expose an independent PDF character layer. The evaluator
maps only character coordinates into the source crop, never character values, and
finds 4,184/4,184 glyph centers in the same column under header lanes and consensus.
The scanned Slater panel has no glyph layer and remains an explicit no-reference
case. The frozen comparison is in `tests/column_geometry_methods_corpus.json`.

Every admitted and refused layout is also run through clean, 150-dpi, blur,
contrast, JPEG, adaptive-binarization, and combined conditions:

```bash
uv run python scripts/eval_column_geometry_degradation.py --check
```

Across 42 layout cases and 798 rows, fixed header lanes yield 791 exact rows, zero
wrong mappings, and seven refusals. Pixel-only repeated-row consensus yields 655
exact rows, zero wrong mappings, and 143 refusals. Typed consensus has the same
result. Most additional refusals occur when downsampling or blur bridges the narrow
GRASP NIXIV `J` and parity columns, removing the separator from the pixels. The
locator refuses those rows instead of guessing. Its degradation result is pinned in
`tests/column_geometry_degradation_corpus.json`.

The fixed-lane result shows the useful direction, but it assumes the header lanes are
already established. Born-digital PDF glyph coordinates can establish them without
supplying values; scanned tables still need a separately measured header locator.
Repeated-row consensus therefore remains evaluation-only for value production.
