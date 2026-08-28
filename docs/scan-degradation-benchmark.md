# Controlled scan-degradation benchmark

This benchmark separates scan quality from table content. It takes Table III from
Burkatzki, Filippi, and Dolg, crops the native PDF table at 300 dpi, and creates one
raster-only PDF page per degradation regime. The source SHA-256 and all 162 numeric
cells are pinned independently of MinerU in
`tests/scan_degradation_ground_truth.json`.
The builder strips image timestamps and suppresses PDF creation dates, so repeated
builds with the same tool versions are byte-identical as well as pixel-identical.

The 12 regimes vary one factor at two severities, plus a clean baseline and a combined
case:

- Effective resolution: 150 and 100 dpi.
- Gaussian blur: sigma 1.2 and 2.0 pixels at 300 dpi.
- Rotation: 1 and 2 degrees.
- Contrast: ImageMagick brightness/contrast values `0x-35` and `0x-55`.
- JPEG compression: quality 25 and 10.
- Combined: 100 dpi, sigma 1.2 blur, `0x-35` contrast, 1.5 degree rotation, and
  JPEG quality 40.

Build, convert, and evaluate it with:

```bash
uv run python scripts/build_scan_degradation_corpus.py

uv run pdf2md convert output/pdf/dolg-table-iii-scan-degradation.pdf \
  --out out/scan-degradation \
  --engine mineru \
  --mineru-executable env/mineru/bin/mineru \
  --table-ocr-executable tesseract \
  --no-formula \
  --no-digitize

uv run python scripts/eval_scan_degradation.py \
  out/scan-degradation/<document-id>/<version> \
  --corpus-manifest output/pdf/dolg-table-iii-scan-degradation.manifest.json \
  --report out/scan-degradation/report.json
```

The focused leave-one-factor-out corpus uses the same source table and ground truth:

```bash
uv run python scripts/build_scan_degradation_corpus.py --suite combined-ablation

uv run pdf2md convert output/pdf/dolg-table-iii-combined-ablation.pdf \
  --out out/scan-ablation \
  --engine mineru \
  --mineru-executable env/mineru/bin/mineru \
  --table-ocr-executable tesseract \
  --no-formula \
  --no-digitize

uv run python scripts/eval_scan_degradation.py \
  out/scan-ablation/<document-id>/<version> \
  --corpus-manifest output/pdf/dolg-table-iii-combined-ablation.manifest.json \
  --report out/scan-ablation/report.json
```

The evaluator aligns only a unique normalized row label followed by six fixed-order
cells. A missing or ambiguous row is `tool_refused`; an emitted wrong value is
`disagree`. This prevents a positional guess from turning a lost chemical identity
into apparently correct numeric data.

## Results

With conservative deskewing enabled, MinerU recovered 1,926 of 1,944 cells exactly,
with zero wrong emitted values and 18 structural refusals. The remaining refusals are
three lost row labels in the combined low-resolution, blur, contrast, JPEG, and
rotation case.

| Regime | Primary agree | Primary refused | Tesseract agree | Tesseract disagree | Tesseract refused |
|---|---:|---:|---:|---:|---:|
| Clean 300 dpi | 162 | 0 | 157 | 5 | 0 |
| Resolution 150 dpi | 162 | 0 | 152 | 4 | 6 |
| Resolution 100 dpi | 162 | 0 | 152 | 10 | 0 |
| Blur sigma 1.2 | 162 | 0 | 157 | 5 | 0 |
| Blur sigma 2.0 | 162 | 0 | 113 | 49 | 0 |
| Skew 1 degree | 162 | 0 | 149 | 7 | 6 |
| Skew 2 degrees | 162 | 0 | 150 | 6 | 6 |
| Contrast -35 | 162 | 0 | 151 | 11 | 0 |
| Contrast -55 | 162 | 0 | 154 | 8 | 0 |
| JPEG quality 25 | 162 | 0 | 152 | 4 | 6 |
| JPEG quality 10 | 162 | 0 | 154 | 8 | 0 |
| Combined | 144 | 18 | 126 | 18 | 18 |

The retained `best_value` is 1,926/1,926 exact wherever row identity survives. The
primary extractor is insensitive to every isolated degradation tested after deskewing;
blur mainly hurts the auxiliary reader. The remaining failure requires several
degradations at once.

## Multi-family degradation extension

The original benchmark holds table content fixed. The extension changes both table
family and rendering condition, using 42 source-checked cells from five tables:

| Family | Typography and difficult features | Cells per variant |
|---|---|---:|
| GRASP fixed-width | Negative signs, long decimals, fixed columns, table rules | 10 |
| Fischer serif | Superscript footnotes, signs near rules, placeholders | 12 |
| Slater book scan | Curvature, faint printing, side-by-side panels | 8 |
| GRASP scientific notation | Positive and negative exponents, superscripts | 8 |
| NASA dot matrix | Leading-dot decimals, faint mixed text and numerics | 4 |

Each table has a clean control and ten derived pages: 150 dpi, 100 dpi, blur,
1.5-degree skew, reduced contrast, adaptive binarization, JPEG quality 25, 100 dpi
plus reduced contrast, skew plus blur, and a combined hard condition. This produces
55 pages and 462 labelled cell evaluations. Source crops, labels, generated page
pixels, the corpus PDF, the extraction provenance, the runtime, and the report are
hash-pinned in `tests/multifamily_degradation_corpus.json`.

Build and check the corpus with:

```bash
uv run python scripts/build_multifamily_degradation_corpus.py

uv run python scripts/eval_multifamily_degradation.py \
  out/multifamily-degradation/263f2589e86a0d53/v1 \
  --corpus-manifest output/pdf/multifamily-table-degradation.manifest.json \
  --runtime tests/multifamily_degradation_runtime.json \
  --report out/multifamily-degradation/final-report.json \
  --check
```

The measured extraction used MinerU 3.4.4 with
`opendatalab/MinerU2.5-Pro-2605-1.2B` on an RTX 4090. The Docker image, upstream and
modified Dockerfiles, base image, model snapshot, packages, GPU driver, CUDA report,
and transport wrapper are recorded in `tests/multifamily_degradation_runtime.json`.

| Family | Primary exact | Primary wrong | Primary refused | Tesseract exact | Tesseract wrong | Tesseract refused |
|---|---:|---:|---:|---:|---:|---:|
| Fischer serif | 130 | 0 | 2 | 0 | 0 | 132 |
| GRASP fixed-width | 100 | 1 | 9 | 0 | 0 | 110 |
| GRASP scientific notation | 88 | 0 | 0 | 48 | 30 | 10 |
| NASA dot matrix | 0 | 0 | 44 | 0 | 0 | 44 |
| Slater book scan | 80 | 0 | 8 | 79 | 1 | 8 |
| **Total** | **398** | **1** | **63** | **127** | **31** | **304** |

The clean control and every non-binarized degradation each yield 38 exact primary
values, zero wrong values, and four refusals. Those four refusals are the NASA cells:
MinerU classifies the crop as a figure even before degradation. This is a structural
detection failure, so the NASA pages do not measure degradation sensitivity.

Adaptive binarization is the distinct failure boundary. It yields 18 exact values,
one wrong value, and 23 refusals. The wrong GRASP value changes
`-1443.2223116` to `2223116`; the source crop shows that thresholding breaks the sign
and decimal groups into separated ink segments. Tesseract refuses the cell, and
`best_value` conservatively retains the wrong primary value at low confidence. This
case is kept as a regression target rather than repaired with a document-specific
rewrite.

The primary and `best_value` totals are identical. The second reader makes no hidden
correction, and its low coverage on fixed-width and serif families rules out treating
it as a general verifier. These results measure controlled failure behavior, not
population error prevalence and not a calibrated promotion threshold.

## Conservative deskew result

The preprocessor examines only textless raster pages. It applies a correction only
when the projection sharpness improves by at least 5 percent and the best angle is at
least 0.75 degrees but below the 3-degree search boundary. Other pages are imported
unchanged. Engine boxes are mapped back onto the original page coordinates, so source
crops retain the original scan and record `processing_deskew_degrees`; Tesseract
independently corrects the crop and records `reader_deskew_degrees`.

The detector selected exactly the intended pages and angles: page 6 at +1 degree,
page 7 at +2 degrees, and page 12 at +1.5 degrees. Primary agreement rose from 1,836
to 1,926 cells, refusals fell from 108 to 18, and disagreements remained zero. The
isolated 1- and 2-degree cases both rose to 162/162. Tesseract agreement rose from
1,642 to 1,767, disagreements fell from 167 to 135, and refusals fell from 135 to 42.

An adjacent false-positive audit found no selected labelled table crops in the
original 61-cell Fischer/Slater scan corpus. The two Fischer PDFs expose text layers and are
excluded from page replacement. Of 511 textless Slater pages, only page 251 was
selected at -1 degree; visual inspection confirms its printed baselines are tilted.

## Combined degradation ablation

The seven-page focused corpus contains a clean control, the full combined degradation,
and five pages that each remove exactly one factor. Its PDF SHA-256 is
`bcfa4abd836af43094ee5e89c6098f6969bb1500ce991a44be36d038bb7e83f5`. Rebuilding it
with the pinned source and local tool versions produces the same bytes.

| Variant | Primary agree | Primary refused | Rows recovered from full case |
|---|---:|---:|---|
| Clean control | 162 | 0 | HCl, ClO, ClF |
| Full combination | 144 | 18 | none |
| Without 100 dpi downsampling | 162 | 0 | HCl, ClO, ClF |
| Without blur | 150 | 12 | HCl |
| Without contrast reduction | 162 | 0 | HCl, ClO, ClF |
| Without rotation | 156 | 6 | HCl, ClO |
| Without JPEG compression | 156 | 6 | HCl, ClO |

All 1,092 emitted primary cells match the pinned values; the other 42 cells are
whole-row refusals. No leave-one-out page introduces a wrong numeric cell. The full
case changes three labels while retaining their six values: `HCl` becomes `HCI`,
`ClO` becomes `CIO`, and `ClF` becomes `CIF`. Removing either downsampling or contrast
loss restores all three identities. Blur, rotation, and JPEG damage decide which
labels cross the ambiguity boundary, but none is the sole cause.

The evidence points to low effective resolution plus contrast loss as the common
limiting interaction. A general `I`-to-`l` rewrite would also corrupt legitimate
labels, and stronger global preprocessing could regress the 1,092 exact cells.

## Text-valued key reader trial

The focused key evaluator compares each primary row label with two Tesseract paths:
the aligned whole-table read and a separately segmented isolated-key read. Unicode
compatibility, case, punctuation, and subscripts are normalized, but `I`, `l`, `1`,
and bar glyphs remain distinct. A key is accepted only when all three candidates
agree and none contains one of those unresolved glyphs.

```bash
uv run python scripts/eval_table_keys.py \
  out/scan-ablation/<document-id>/<version> \
  --corpus-manifest output/pdf/dolg-table-iii-combined-ablation.manifest.json \
  --report out/reviews/table-key-reader/report.json
```

Across 189 pinned row keys, the primary is correct on 182 and wrong on seven. Plain
agreement between the primary and both Tesseract paths confirms 99 correct keys but
also confirms five wrong `HCI`/`CIF` readings. The explicit confusable-glyph guard
removes those false confirmations: 85 keys are verified, zero are wrong, and 104 are
refused. Refusals comprise 59 disagreements between Tesseract modes, 24 disagreements
between the primary and Tesseract consensus, 19 confusable glyphs, and two missing
isolated reads.

The evaluator can write the refused crops with `--paddle-manifest` and score a
preserved run with `--paddle-run`. All 102 localized crops were inspected and contain
exactly one label. PaddleOCR-VL 1.6's layout-parsing service still refuses all 104
routed keys: 98 empty text results, four request failures, and two crops unavailable
from Tesseract geometry. Padding the unchanged glyphs to a 640 by 192 white canvas
removes most service errors but does not produce text, confirming that a document
layout endpoint is unsuitable for isolated labels.

The two-pass Tesseract gate is safe on this corpus but too sparse to ship at 45 percent
coverage. The next experiment needs a recognition-only line OCR model and row-key
labels from multiple documents and typefaces. Production output continues to retain
the original label and structural refusal.

## Recognition-only line reader

The follow-up benchmark uses the pinned `PP-OCRv6_medium_rec` model through
PaddleOCR 3.7.0. The corpus contains 231 crops: 187 Dolg labels across the clean and
six combined-ablation pages, 12 Fischer dot-matrix radius labels, 12 Slater table
keys, and 20 held-out GRASP2018 orbital keys in monospaced program output. The first
three sources selected the fixed 0.99 rule; the fourth was labelled and scored only
after that choice. Each document is pinned by its source PDF SHA-256. Expected labels
stay out of the inference manifest, and the run records each input hash, stable model
artifact hash, package versions, text, and model score.

```bash
.venv/bin/python scripts/eval_line_reader.py \
  --output-dir out/reviews/line-reader-v6

python scripts/run_paddle_line_reader.py \
  out/reviews/line-reader-v6/inputs.json \
  out/reviews/line-reader-v6/run.json \
  --model PP-OCRv6_medium_rec \
  --device gpu:0

.venv/bin/python scripts/eval_line_reader.py \
  --output-dir out/reviews/line-reader-v6 \
  --run out/reviews/line-reader-v6/run.json
```

The raw reader accepts 149 outputs at the predeclared 0.99 threshold: 147 are exact
and two are wrong (`ClF` becomes `CIF`; Fischer `0.0008` becomes `O.COOB`). The
production rule also requires the normalized reader output to match the primary
extraction. Both wrong reads disagree with the primary and are refused, leaving 147
confirmed keys, zero false confirmations, and 84 refusals. The held-out source alone
has 20 confirmations, zero false confirmations, and zero refusals.

Agreement normalizes case, Unicode subscripts, whitespace, and Unicode minus variants.
Decimal points and signs remain significant: `0.0008` cannot agree with `00008`, and
`3p-` cannot agree with `3p`. This is stricter than the initial punctuation-insensitive
diagnostic and does not change any observed result in the four-document run.

| Minimum score | Confirmed correct | False confirmations | Refused |
|---:|---:|---:|---:|
| 0.900 | 198 | 4 | 29 |
| 0.950 | 187 | 1 | 43 |
| 0.980 | 172 | 0 | 59 |
| 0.990 | 147 | 0 | 84 |
| 0.995 | 123 | 0 | 108 |
| 0.999 | 86 | 0 | 145 |

The low thresholds reproduce the known correlated glyph failures. At 0.95 the model
and primary both call one `ClF` label `CIF`; at 0.90 there are four false
confirmations. This makes the corpus a meaningful safety test rather than a clean-only
accuracy sample. The held-out result clears the fixed gate, so the reader is now an
optional post-conversion evidence stage. PaddleOCR remains outside the default local
environment. The stage writes a hash-pinned sidecar and never rewrites extracted
values. An end-to-end run prepared a fresh crop set from the stored GRASP conversion:
18/20 keys met the fixed threshold and agreed with the primary, while correct reads of
`4p-` and `6p-` scored below 0.99 and were conservatively refused.

The stable model fingerprint covers `inference.json`, `inference.pdiparams`, and
`inference.yml`. Downloader cache metadata is excluded because it contains local
download timestamps and previously made identical model bytes appear different.

### Repeated-panel production locator

The production locator proves the boundary gap between side-by-side panels before it
uses either of two paths. The ordinary path requires one panel with the exact expected
number of numeric key lines and unique vertical matches in the other panels. When the
extracted lanes contain different row sets, the fallback matches keys independently
inside each panel's source bounds. A recovered lane must be numeric and increasing;
missing, ambiguous, repeated, and nonincreasing matches are refused individually.

Two Fischer layouts exercise the path: a two-panel, 16-column table on page 30 and an
unequal-width three-panel table on page 18. The locator prepared all 387 printed key
cells with zero preparation refusals. The pinned reader confirmed 318 cells and
refused 69 below-threshold reads; there were no above-threshold reader-primary
disagreements. In the 12-cell page-30 panel covered directly by source-pinned labels,
it confirmed nine, refused three, and falsely confirmed none.

Repeated labels remain separate per-cell evidence. An accepted read in one panel is
not copied to another panel, even when the printed row-key sequences appear identical.

The complete Fischer radial-table run covers 131 source blocks on pages 18 through
86. It can be reproduced without a block list:

```bash
pdf2md line-reader prepare out/0685e8d85e2237d8/v5 \
  out/reviews/fischer-line-reader-all-radial-v5 \
  --page-from 18 --page-to 86
```

The locator prepares 7,886/8,143 extracted key cells. Preserving each panel's source
rows raises the denominator from 8,100 to 8,143; the old synchronized row list hid 43
keys in staggered lanes. The denominator still comes from the extracted grid and does
not claim printed rows that MinerU omitted. The 257 unavailable cells now correspond
one-to-one with explicit refusal events:

| Layout family | Tables | Prepared | Expected | Unavailable |
|---|---:|---:|---:|---:|
| Three-panel header | 3 | 805 | 861 | 56 |
| Two-panel header | 10 | 1,115 | 1,164 | 49 |
| Two-panel continuation | 7 | 686 | 686 | 0 |
| Single panel | 111 | 5,280 | 5,432 | 152 |

Five malformed or split repeated-header blocks on pages 19, 20, 26, 27, and 28 now
prepare 861/964 extracted keys and account for 103 unavailable cells. Each crop is
localized from its own panel and source row; no neighboring lane supplies a value or
row identity. The seven headerless continuation blocks remain at 686/686. Single
panels account for 152 unavailable cells, concentrated in tables whose row-key
geometry Tesseract cannot locate uniquely.

The pinned reader confirms 6,621 of the 7,886 prepared crops. It refuses 1,244 below
the fixed 0.99 score and records 21 above-threshold disagreements. All 7,025 crops
shared with the earlier run have identical geometry, hashes, reader text, scores, and
statuses. The 861 recovered crops add 721 agreements, 138 low-score refusals, and two
punctuation disagreements (`18.000` versus `18.00`, and `0.500` versus `0,500`). The
previously reviewed digit-changing disagreements are unchanged. No table value is
rewritten.

### Source-row reconstruction trial

The malformed grids contain a second defect below the missing keys. On pages 26 and
28, MinerU sometimes attaches a later printed radius to values from an earlier source
line. A correct recognition-only read of the radius therefore cannot validate the
rest of that structured row.

`scripts/eval_source_row_recovery.py` treats the source panel as a separate evidence
layer. It derives the longest strictly increasing row-key sequence repeated across at
least two source blocks; the Fischer corpus yields the 97-radius sequence in nine
independent blocks. The ordinary path requires exactly 97 numeric source lines. The
one-gap path requires one unique interior gap, exact source keys on both sides, at
least 85 percent exact key matches over the remaining lines, and exactly one
intervening source line in the same key bounds. Its corrupted key is sent to the
pinned reader as a panel-level gate. A second row locator reads only source pixels. It
finds dominant vertical-whitespace gutters for the structural panel count, projects
each panel's leading key-column stripe onto the vertical axis, removes horizontal
rules and undersized ink bands, and selects the expected row sequence. Missing or
ambiguous gutters are refused. It does not read OCR tokens or use Tesseract's column
boxes. Every inferred OCR line must fall inside the corresponding projection band.
Within each accepted row, horizontal dark-pixel runs are joined only across gaps no
larger than one tenth of the structural column pitch. The row must produce the exact
column count, and each resulting run center must land inside the matching OCR-derived
cell box. Each later row must still pass its own source-key read. Tesseract and the
reader must agree before a data cell becomes a candidate. No candidate is written
into the normalized tables.

Across the five malformed blocks, the one-gap path admits the page-28 right panel and
keeps both 48-line page-27 halves refused. The trial prepares 1,089 crops across 204
recovery rows, 48 controls, and two inferred-key checks. Of the recovery rows, 146
pass their own source-key gate; 98 contain a usable data value, for 295 two-reader
data-cell candidates. Controls contribute 168 accepted cells that exactly match the
structured grid, zero disagreements, and 108 refusals. All 945 crops shared with the
exact-count trial retain identical geometry, hashes, reader text, scores, and status.

Paddle confirms the page-28 inferred key as `0.100` at 0.9938 confidence, then admits
18 of its 27 recovery keys and 17 data values. Source-crop review finds all 17 values
correct. The healthy page-20 panel provides a useful negative control: its inferred
`7.000` crop reads `7.C00` at 0.9814 and remains refused under the frozen threshold.
The independent locator finds 97/97 row bands in both inferred panels, and all 194 OCR
row centers land inside their corresponding bands. The two incomplete page-27 panels
have only 51 projection bands and refuse before this gate can authorize an inferred
alignment. Reader inputs and all 1,089 crop hashes are unchanged from the preceding
trial.

Together with the earlier sample, the source-labelled set has 41 agreements, zero
disagreements, and zero refusals. The labels were selected after the acceptance gate,
so they test admitted errors rather than estimate held-out accuracy. The remaining
refusal rate and single-document labels keep this as an evaluation artifact.

### Cross-document one-gap alignment corpus

The Fischer result was selected after observing the failure, so it cannot establish
that the one-gap rule generalizes. A separate controlled corpus freezes five source
panels from three PDFs before perturbation: two 26- and 41-row GRASP energy tables,
two 10-row convergence tables from the stability paper, and the 19-row right half of
Slater's Thomas-Fermi table. Their complete source pages and row schemas contain 527
numeric cells.

`scripts/eval_source_row_alignment_corpus.py` reruns Tesseract on the hash-pinned
source crops, then changes only selected key tokens in its TSV. Source pixels and data
cells remain unchanged. Every panel has seven cases: an unmodified calibration, one
valid interior gap, and five required refusals covering an edge gap, two gaps, a
broken anchor, two possible intervening lines, and exact-key agreement below 85
percent. The comparator checks the vertical identity of every restored source line,
not merely the inferred gap number. It also derives row geometry directly from each
source crop. The key stripe width comes from the number of columns in that panel, not
from OCR word boxes.

All 35 case decisions match the frozen reference. The five valid gaps restore the
original mapping for 527/527 numeric cells, with zero wrong mappings and zero
refusals. The independent locator matches 106/106 held-out source rows with zero band
mismatches. All 25 negative cases refuse for their pinned structural reason. The
26-row GRASP panel is not a perfect control: Tesseract naturally merges row and
position tokens for two rows, leaving 24/26 exact baseline keys and 23/25 after the
controlled gap. Its correct alignment therefore exercises the 85 percent rule with
existing OCR noise.

This is a structural perturbation benchmark. It shows that the aligner handles these
known failure shapes across three documents, but says nothing about how often those
shapes occur naturally or whether a mapped value is recognized correctly. It does
not justify automatic replacement or weaken the independent-reader gate.

### Projection-row stress corpus

The held-out alignment corpus tests missing-key structure on otherwise unchanged
source pixels. `scripts/eval_projection_row_stress.py` separately tests the
token-free row locator against deterministic pixel changes. It freezes one 97-row
Fischer radial panel, one 10-row stability table, and one 41-row GRASP table by source
PDF, page, block, provenance, and rendered-crop hashes. Baseline Tesseract supplies
source-checked row centers only to the reference side; the locator receives the
transformed pixels and the structural panel geometry.

The 28 cases cover skew with and without the existing deskew pass, vertical and
horizontal crop shifts, salt-and-pepper noise, synthetic page bow, unequal
side-by-side panel widths, blur, horizontal rules, and a false footer row. A new
source-only panel locator finds broad vertical-whitespace gutters, requires each
chosen gutter to dominate any rejected candidate by at least two to one, and excludes
the gutter from both adjacent panel bounds. The cross-check results are:

| Family | Cases | Equal-width exact | Equal-width refuse | Detected exact | Detected refuse |
| --- | ---: | ---: | ---: | ---: | ---: |
| Blur | 2 | 2 | 0 | 2 | 0 |
| Control | 3 | 3 | 0 | 3 | 0 |
| Crop and shift | 5 | 2 | 3 | 3 | 2 |
| Curvature | 3 | 3 | 0 | 2 | 1 |
| Noise | 4 | 2 | 2 | 2 | 2 |
| Skew and deskew | 6 | 2 | 4 | 6 | 0 |
| Unequal panels | 5 | 2 | 3 | 5 | 0 |
| **Total** | **28** | **16** | **12** | **23** | **5** |

The detected-bound path has 23 raw agreements, four raw disagreements, and one raw
refusal. It restores all five 30/70 through 70/30 panel layouts, all six skew cases,
and the right-shift case. A 32-pixel synthetic bow becomes a conservative refusal,
while clipped data rows, dense noise, and the false footer still produce shifted raw
sequences. The row cross-check refuses all five unsafe or ambiguous cases, leaving
zero accepted wrong mappings.

The same detector finds both gutters in the two real three-panel Fischer crops and
the single gutter in each real two-panel crop. Re-preparing the source-row overlay
keeps all 1,089 cell geometries and hashes byte-identical. Its 146/204 confirmed
recovery keys, 168 accepted controls, and 41/41 source labels are unchanged. The
incomplete page-27 panels still refuse on row count.

`scripts/eval_projection_panel_corpus.py` widens the boundary comparison to all
available radial-table evidence. It compares detected bounds with 7,886 hash-pinned
key-cell centers and their structural panel identities. All 130 tables with reference
cells agree, including 17 two-panel and three three-panel tables; there are zero
boundary disagreements and zero detector refusals. One single-panel table has no
prepared key-cell reference and is reported as `no_reference`, not as an agreement.

### Projection-column corpus

`scripts/eval_projection_column_corpus.py` reruns panel, row, and column projection
against the source-row recovery crops. Its independent reference is the frozen subset
of cell IDs, source hashes, crop hashes, and OCR-derived cell boxes that existed before
the column locator. The source-pixel path receives the structural panel, row, and
column counts, but no OCR tokens or word boxes.

All 1,089 prepared cells agree: every independently detected ink-run center lands
inside its assigned reference box. The seven panels carrying those references pass
with zero disagreements and zero column refusals. Five other panel records have no
prepared cells and remain `no_reference`. Re-preparing the recovery overlay adds a
`projection_x_run` to every cell while retaining the same 1,089 IDs, crop geometries,
crop hashes, reader outputs, 146/204 confirmed recovery keys, 168 accepted controls,
and 41/41 source labels.

The 28-case degradation corpus measures the column locator's coverage boundary after
the panel and row gates. It finds exact structural columns in 1,602/1,792 available
rows. Sixteen cases pass every row; 12 refuse at least one row. All unequal-width
layouts and clean controls pass. Uncorrected skew, 16-pixel bowing, sparse pixel noise,
and one blur setting produce local refusals. Deskew restores both one-degree cases.

The five-panel held-out corpus supplies a typography check. The locator finds exact
columns on 79/106 rows: all rows in the GRASP N IX, stability Table III, and Slater
panels, nine of ten stability Table II rows, and none of the 26 GRASP Mg III rows. The
last panel's long configuration strings split into nine or ten runs against eight
structural columns. This is a useful refusal, not evidence for increasing the merge
distance, because a larger gap would merge the narrow `No`, `Pos`, `J`, and `Parity`
columns on other GRASP tables.

### Held-out key recognition

The row-geometry corpus also provides 106 source-checked key values across those five
panels. `scripts/eval_heldout_key_reader.py` holds the recognizer fixed and changes
only its crop geometry. The reference path uses Tesseract word boxes with production
padding. The projection path takes the full source-pixel ink envelope inside the
pre-established key lane, without OCR token geometry.

At the frozen 0.99 score threshold, the reference path has 103 agreements, one
disagreement, and two refusals. The projection path has 106 agreements and no
disagreements or refusals. A semantic refusal-only fallback uses the reference result
for 103 keys and projection for three, yielding 106/106. Both GRASP reference misses
include the adjacent position digit in the crop (`2 1` and `4 1`). The Slater
reference crop reads `2.2` correctly but scores 0.84585; projection reads `2.2` at
0.99996. The result is frozen in `tests/heldout_key_reader_corpus.json`, including
source, crop, model, and run hashes.

This closes the recognition check for row keys. It does not establish accuracy for
arbitrary data cells, whose typography, signs, exponent notation, and column spacing
are less constrained.

### Held-out data-cell recognition

A second source-labelled corpus selects 56 actual cells from six panels in three PDFs.
The labels were read from the source crops before the auxiliary-reader run. Fixed
structural lanes supply the data-column bounds; within each lane, the projection path
uses only the independently located row band and source-pixel ink envelope. The
reference path keeps the production Tesseract word-box crop.

The primary extraction is exact on 56/56. Tesseract is semantically exact on 51/56;
all five errors
are superscript `a` footnotes recognized as an additional trailing digit. PP-OCRv6 on
the reference crops accepts 52/56 with no disagreement and four refusals. The
projection crops accept 51/56 with no disagreement and five refusals. A refusal-only
crop fallback uses 52 reference reads and one projection read, producing 53 exact
confirmations and three refusals. Projection rescues `-0.02146`, whose reference
crop score is 0.98609 versus 0.99481 from projection. Both paths refuse footnoted
`0.10865` below 0.99. Every path reads eight scientific-notation cells exactly.
Tesseract preserves two vertical-ellipsis placeholders as `:`, while PP-OCRv6 refuses
both at the fixed confidence gate.

The crop, model, run, source, and outcome hashes are pinned in
`tests/heldout_data_reader_corpus.json`. This is a clean-cell false-alert and coverage
test. It has no primary error, so it cannot estimate correction recall.

### Rendering-factor stability

`scripts/eval_rendering_stability.py` rerenders those same 56 labelled cells from the
source PDFs and holds PP-OCRv6 fixed across a 3 by 2 by 2 by 2 design: 300, 450, and
600 dpi; grayscale and adaptive binarization; original and deskewed pixels; tight and
moderately padded crops. The manifest contains 1,344 reads. All six 300 dpi table
renders exactly reproduce the earlier source crops.

At the frozen 0.99 reader gate, all 24 conditions have zero accepted wrong values.
Coverage ranges from 45/56 for 300 dpi adaptive tight to 53/56 for 600 dpi grayscale
tight. The deskew detector estimates zero degrees for all 18 source-table renders, so
the original and deskewed conditions duplicate each other exactly. The experiment
therefore has 672 unique crop hashes and supplies no natural deskew comparison.

Instability is calculated from the 23 non-baseline conditions before checking the
300 dpi grayscale padded baseline. Fifteen cells are unstable. They include all seven
baseline refusals, while none of the 41 stable cells refuses. This gives 1.0 refusal
sensitivity, 0.8367 specificity, 0.4667 positive predictive value, and 1.0 negative
predictive value on this frame. The primary extraction is exact on 56/56, so the run
cannot test whether instability predicts primary errors. Shared-render agreement is
triage evidence, not independent verification.

The crops, source labels, reader model, raw run, and exact outcome are hash-pinned:

```bash
.venv/bin/python scripts/eval_rendering_stability.py prepare
.venv/bin/python scripts/eval_rendering_stability.py compare \
  out/reviews/rendering-stability-v1/run.json --check
```

### Projection-crop differential

`scripts/eval_projection_crops.py` crops the same 1,089 Fischer cells directly from
the independent row bands and column ink runs. It does not use OCR word boxes. The
reader preprocessing and pinned PP-OCRv6 identity stay unchanged, so crop geometry is
the only experimental variable.

The raw reader values agree for 1,016/1,088 comparable cells, with 72 disagreements
and one reference-side empty read. The projection crops retain 41/41 raw agreements
on the source-reviewed labels, but four scores fall below 0.99. They are therefore a
poor replacement for the existing crops. As a refusal-only fallback, they retain all
41 reviewed labels and recover 88 cells above threshold that the existing crop path
refuses. After the row-key and Tesseract-agreement gates, accepted recovery cells rise
from 441 to 541 and refusals fall from 370 to 270.

The fallback also exposes two structured-control divergences in one shifted row. Both
crop paths and Tesseract read `-0.0653` followed by `0.1408`; source review confirms
those values, while the structured row had shifted `0.1408` left and dropped the
following cell. The two source adjudications are hash-pinned separately from the
41-cell selection. Reproduce the local comparison after the pinned GPU reader run
with:

```bash
uv run python scripts/eval_projection_crops.py compare \
  out/reviews/fischer-projection-crops-v1/run.json --check
```

### Integrated source-row fallback

The v3 source-row overlay now prepares both crop paths at the same time and accepts a
projection result only after the original crop fails its full semantic gate. A key
crop must match the repeated row template; a data crop must agree with Tesseract. The
output records `accepted_reader` for each candidate and retains both raw reader values,
scores, refusal reasons, boxes, and hashes.

This integration preserves all 610 v2 candidates with identical value strings and
adds 137 candidates. Of those additions, 84 use a projection read and 53 use a valid
original read that was previously blocked by an unconfirmed row key. Confirmed
recovery keys rise from 146/204 to 162/204. The final overlay contains 747 accepted
candidates, with 41/41 original source labels and 2/2 divergence adjudications. The
two structured-control disagreements remain visible because the pixels prove the
structured row was shifted.

The integration comparison is independently hash-pinned:

```bash
uv run python scripts/eval_source_row_fallback.py --check
```

The held-out alignment run remains 35/35 cases and 527/527 one-gap cell mappings. The
28-case degradation corpus, 130-table panel corpus, and 1,089-cell column corpus also
retain their exact frozen outcomes.

## Alignment defect found by the benchmark

The first run produced 493 Tesseract disagreements and 395 refusals. The fallback
layout treated chemical labels such as `Li2`, `N2`, and `Cl2` as numeric because they
contain digits. Sparse label positions then shifted the inferred column centers.

The existing layout remains the first choice. For a single-panel table, a missing or
geometrically collapsed set of column centers now triggers one retry using only source
columns typed as numeric or placeholders. This excludes chemical row labels without
changing the ordinary path for repeated panels and other table shapes. On a fresh
end-to-end run of the identical PDF, Tesseract agreement rose from 1,056 to 1,642
cells, disagreements fell from 493 to 167, and refusals fell from 395 to 135.
High-confidence cells rose from 1,056 to 1,642; low-confidence cells fell from 694 to
93. The remaining reader disagreements are OCR differences such as doubled or lost
minus signs, plus failures under the two-degree skew, rather than a global column
shift.

This is an exploratory benchmark, not a CI gate. One font and one table cannot define
a general scan-quality threshold. The corpus and evaluator are persistent regression
fixtures, but promotion thresholds need labelled tables from other documents and
typefaces.
