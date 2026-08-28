# Engine bake-off results

These are measured checkpoints, not a parser decision. Each row points to a
pinned source in `tests/bakeoff_manifest.json`; `scripts/score_bakeoff.py`
compares native structured output against `tests/bakeoff_labels.json`.

## 2026-08-11: deterministic vector charts

Environment: Apple M2 Ultra, macOS 26.6, Docling 2.93.0, Docling Core 2.74.1,
Docling IBM Models 3.13.2, and Docling Parse 5.11.0. Docling reported that MLX
was unavailable and ran its picture-description model through Transformers.

| Source | Engine | Pinned facts | Time | Result |
|---|---|---:|---:|---|
| `vector_plot.pdf` | pdf2md current | 4/4 | 6.0 s | pass |
| `vector_plot.pdf` | Docling standard | 4/4 | 292.2 s | pass |
| `bar_plot.pdf` | pdf2md current | 4/4 | 5.8 s | pass |
| `bar_plot.pdf` | Docling standard | 4/4 | 123.3 s | pass |
| `scatter_plot.pdf` | pdf2md current | 4/4 | 5.7 s | pass |
| `scatter_plot.pdf` | Docling standard | 0/4 | 16.9 s | fail: no chart data |
| `scatter_two_series.pdf` | pdf2md current | 4/4 | 5.7 s | pass |
| `scatter_two_series.pdf` | Docling standard | 0/4 | 15.3 s | fail: no chart data |
| `subplots.pdf` | pdf2md current | 4/4 | 5.8 s | pass |
| `subplots.pdf` | Docling standard | 4/4 | 98.0 s | pass |

Each case checks chart count, series count, maximum x-coordinate error, and
maximum y-coordinate error. The tolerances are absolute and pinned per fixture.
The line and bar cases use 0.05 on both axes. The scatter cases allow 0.1 on y.
The multipanel case allows 0.5 on y because its second panel spans 0 to 100.

Docling's native chart table was exact for the line, bar, and multipanel charts.
It classified both scatter figures correctly, at confidence 0.979 and 0.986,
but its chart-data stage emitted no table for either. The incumbent passed all
five fixture classes. This measured gap justifies retaining pdf2md's vector-path
recovery for scatter plots. None of these fixtures tests a real scientific
chart, so it does not settle the rest of the parser choice.

Default policy: keep model-free vector-chart recovery enabled, with
`--no-digitize` as the opt-out. Keep raster/VLM chart estimation behind the
explicit `--digitize-vlm` flag because the raster case does not support treating
model-generated values as authoritative data.

The enabled Docling picture-description enrichment failed a basic output-quality
check on every chart class. It emitted
`In this image, we can see a graph.<end_of_utteranc` for the line, bar, and
multipanel figures. On both scatter plots it hallucinated bar charts, including
100 nonexistent bars and a nonexistent `100%` title in one case. Keep chart
extraction and picture description as separate decisions in the final pipeline;
the current native description model is unsafe as document content.

The Docling Markdown exporter also wrote absolute image paths. Its JSON output
retained the crop and exact chart table, so later comparison should continue to
read native JSON rather than treat Markdown as the whole parser result.

## 2026-08-11: sampled technical-manual table

The runner extracted page 26 from `GRASP2018-manual.pdf` once and gave all four
engines the same derived PDF bytes. The source and sampled-input hashes are
pinned in the labels. The case contains one 23-row, two-column table plus six
reading-order anchors before, through, and after the table.

| Engine | Pinned facts | Time | Result |
|---|---:|---:|---|
| pdf2md current | 29/33 | 6.9 s | fail: three rows and caption order |
| Docling standard | 3/33 | 12.0 s | fail: symbol-font text corruption |
| PaddleOCR-VL | 28/33 | 124.6 s | fail: five exact-cell differences |
| MinerU hybrid high | 33/33 | 21.2 s | pass |

pdf2md found the table with the correct dimensions and recovered 20 of 23 rows
exactly. One wrapped row lost its first cell and the first part of its
description. Two rows inserted a space in `gJ-factors`. The six source snippets
were present, but the Markdown moved the table caption from above the table to
below it, so source reading order failed.

Docling found the table with the correct dimensions but decoded nearly every
character through symbol-font substitutions such as `/a116` and dingbats. It
passed only table count, row count, and column count. None of the six reading
anchors survived. This page establishes that layout detection alone is not a
usable result for broken or unusually encoded fonts.

PaddleOCR-VL recovered the dimensions, reading order, and 18 of 23 rows exactly.
Its failures were three spaces around filename extensions and two substitutions
of the digit `1` for the letter `l` in `.lbl`. MinerU recovered every labelled
cell and all reading-order facts exactly.

A visual reread corrected the labels after the first scoring pass. The source
itself contains `Labels in in LSJ-coupling.` in two rows, so reproducing that
duplication is accurate. The scorer also accepts math markup around `g_J`, but
does not hide a plain-text space inserted before `-factors`.

## 2026-08-11: raster scientific figure

`image.pdf` contains one rotated, dense two-panel scientific chart as a single
embedded raster image. All four engines saved a crop covering at least 75
percent of the page. The nine facts check figure count, page, crop containment,
asset existence, absence of unverified structured data, caption text, both
panel labels, and the Table 5 cross-reference.

| Engine | Pinned facts | Time | Result |
|---|---:|---:|---|
| pdf2md current | 7/9 | 10.7 s | fail: split caption and joined OCR word |
| Docling VLM | 5/9 | 42.9 s | fail: no searchable figure text |
| PaddleOCR-VL | 5/9 | 20.7 s | fail: incomplete caption and no panel labels |
| MinerU hybrid high | 5/9 | 34.6 s | fail: invented chart data and no panel labels |

pdf2md preserved the image, the primary-vortex panel label, and the Table 5
reference. Its caption stopped before the final `center.`, which appeared later
as a separate paragraph, and OCR joined `GEOMETRICCENTER`. Docling VLM produced
the crop and the generic classification `Line chart`, but none of the four
source facts. PaddleOCR-VL retained part of the caption but none of the interior
labels.

MinerU retained the complete caption and crop, then emitted a structured chart
table whose values were not supported by the source. That is a more serious
failure than missing OCR because an agent could treat invented numbers as data.
No engine recovered defensible numeric series from the raster, so the faithful
representation for this case remains the crop plus searchable labels and an
explicit source-page reference.

## 2026-08-11: numbered display equation

Page 34 of `GRASP2018-manual.pdf` contains one numbered transverse-photon
interaction equation. The five facts check equation count, page, number, exact
normalized LaTeX, and whether a source crop remains available. The exact check
preserves semantic typography, including the bold vector notation on
`\alpha` and `\nabla`.

| Engine | Pinned facts | Time | Result |
|---|---:|---:|---|
| pdf2md current | 3/5 | 33.0 s | fail: vector bolding and equation number |
| Docling standard | 2/5 | 36.4 s | fail: vector bolding, number, and crop |
| Docling VLM | 0/5 | 35.9 s | fail: equation omitted |
| PaddleOCR-VL | 3/5 | 189.2 s | fail: gradient bolding and crop |
| MinerU hybrid high | 5/5 | 30.3 s | pass |

pdf2md and Docling standard produced the same structurally complete expression,
including the sum, both interaction terms, indices, gradients, and nested
fractions. Both flattened the bold `\alpha` and `\nabla` glyphs to unbolded
symbols and dropped `(4.1)`. pdf2md also saved a complete equation crop that
retains the omitted distinctions and number. Docling standard did not.

Docling VLM omitted the equation and replaced much of the surrounding prose
with repeated invented text about active sets. This preset is unsafe for
born-digital technical pages in the current environment.

PaddleOCR-VL retained the equation number and bold alpha notation, but flattened
the bold gradient and did not save an equation crop. MinerU retained the exact
normalized LaTeX, number, page, and source crop.

## 2026-08-11: scanned mixed-layout page

PDF page 37 of `slater-quantum_theory_of_atomic_structure-vol1.pdf` is a fully
scanned page with dense prose, four numbered display equations, and a phase-space
diagram embedded in the prose flow. The 31 facts check six reading-order anchors,
the diagram crop and caption, absence of invented structured data, and exact
semantic LaTeX, number, page, and crop retention for every equation.

| Engine | Pinned facts | Time | Result |
|---|---:|---:|---|
| pdf2md current | 23/31 | 124.4 s | fail: order, caption, numbers, and two equations |
| PaddleOCR-VL | 26/31 | 151.1 s | fail: equation crops and one exponent |
| MinerU hybrid high | 31/31 | 24.9 s | pass |

pdf2md retained the full scanned page, complete diagram, and all four equation
crops. It transcribed the first two equations exactly, but dropped every equation
number, corrupted the two harder equations, misread part of the figure caption,
and placed the caption after prose that follows it in the source.

PaddleOCR-VL recovered prose order, the diagram and caption, and all four equation
numbers. Three equation transcriptions were exact. It changed the final
equation's `q^2` to `q^3` and did not save equation-level crops.

MinerU recovered every pinned fact. This is enough evidence to use MinerU as the
primary parser for scanned pages and as the targeted fallback for difficult
tables and equations. Its raster-chart failure still requires a gate that rejects
structured chart data unless an independent check confirms the values.

### Whole-page glm-ocr follow-up

The original incumbent row used pdf2md's default scan path. A second run used
`--ocr-page-vlm --vlm-ocr-model glm-ocr:q8_0` on the same pinned page and scored
12 of 31 facts in 135.6 seconds. The Markdown contained all four numbered
equations, but page-level replacement stored the complete transcription as one
paragraph. The bundle therefore had no equation blocks or equation-level crops,
and the figure crop had no searchable labels. The model also repeated most of
the page three times. The repeat is now trimmed and visibly flagged, but the
missing element structure remains. MinerU's 31-of-31 result still supports a
small native adapter for scanned pages and difficult table/equation regions.

The implemented adapter then passed the same facts through pdf2md's emitted
bundle: 31 of 31 on the Slater scan and 33 of 33 on the GRASP table/order page.
It reads MinerU's native middle JSON, re-renders source crops itself, and does not
import or expose MinerU's generated chart tables.

## 2026-08-25: pinned Docling layout-model sweep

This follow-up tests all five layout models exposed by Docling 2.108.0 on the
four labelled cases that still fail because of layout, table, equation, scan,
or figure handling. It does not pay for another full-book run before a sampled
page shows a quality gain.

The fixed environment is Docling 2.108.0, Docling Core 2.91.0, Docling IBM
Models 3.13.2, Docling Parse 7.13.0, and RapidOCR 3.8.1. Every Docling model is
pinned to an immutable Hugging Face commit, byte count, and weight SHA-256 in
`tests/docling_layout_candidates.json`. Each run verified the local weight and
recorded its pipeline settings, native outputs, wall time, CPU time, and peak
resident memory. Formula recognition, RapidOCR, table recognition, export
settings, device selection, and thread count stayed fixed; only the layout
model changed.

| Engine | Broken-font table | Numbered equation | Scanned mixed layout | Raster figure | Total | Time | Peak RSS |
|---|---:|---:|---:|---:|---:|---:|---:|
| Heron, current default | 3/33 | 2/5 | 19/31 | 5/9 | 29/78 | 151.5 s | 2.62 GB |
| Heron-101 | 2/33 | 2/5 | 19/31 | 5/9 | 28/78 | 151.2 s | 2.70 GB |
| Egret medium | 3/33 | 2/5 | 19/31 | 4/9 | 28/78 | 149.7 s | 2.48 GB |
| Egret large | 0/33 | 2/5 | 19/31 | 5/9 | 26/78 | 152.4 s | 2.46 GB |
| Egret xlarge | 2/33 | 2/5 | 19/31 | 5/9 | 28/78 | 155.3 s | 2.64 GB |
| MinerU hybrid high | 33/33 | 5/5 | 31/31 | 5/9 | 74/78 | 111.0 s | not recorded |

The layout swap cannot repair the broken character encoding in the GRASP
table. Heron and Egret medium retain its dimensions but reproduce corrupted
text. Heron-101 and Egret xlarge also lose a row, while Egret large misses the
table. Every model gives the same 2/5 equation result: it finds the equation but
drops its number, semantic bolding, and source crop.

All five Docling models produce the same 19/31 result on the scanned Slater
page. They lose source order, one figure phrase, all equation numbers and
equation crops, and two exact equation transcriptions. Their runtime differs by
about two seconds and their peak RSS by 0.23 GB, with no quality gain. On the
raster figure, all five save a safe source crop but recover none of the four
required text strings. Egret medium also makes the crop slightly too small.

The same MinerU 3.4.4 runs remain the relevant comparison because the installed
package and model configuration have not changed. `tests/bakeoff_engine_pins.json`
records its dependency versions and the two model snapshot commits from a
configuration that predates those runs. Peak RSS was not captured by the older
runner, so no memory comparison is claimed. MinerU passes the table, equation,
and scan cases, then fails four raster facts and emits unsupported chart data.

Decision: retain Heron as the Docling default and retain MinerU as a targeted
fallback for scans and difficult tables or equations. Do not promote any
Docling layout alternative, and do not route raster charts through MinerU
without an independent data check. The full portable result, including all
fact-level outcomes, source hashes, model pins, dependency versions, timings,
and Docling memory measurements, is in
[`layout-bakeoff-2026-08-25.json`](layout-bakeoff-2026-08-25.json).

## Current outcome

The document bundle uses content-specific routing, including the MinerU adapter
and pdf2md's chart-safety gates. The pinned layout sweep found no reason to
change those defaults or pay for full-book runs. New clean-paper, two-column,
and complex-table cases can extend the regression corpus when suitable examples
become available.
